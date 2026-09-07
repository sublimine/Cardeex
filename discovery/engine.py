"""Governed discovery execution, pure replay and finite link expansion.

Inventory details/photos, search-engine scraping and entity resolution are not
side effects of this worker. Every success is a document-level observation.
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import time
from urllib.parse import urlsplit

from .adapters import PARSER_VERSION, ParserError, ParserLimitError, parse_document
from .model import CLASSES, SOURCE_TYPES, Conflict, Observation, digest, instant, timestamp
from .transport import (AccessPolicy, FetchLimitError, PolicyError, RateLimited,
                        RobotsDenied, SafeFetcher, TransportError, normalize_url)


def observations_from_document(body: bytes, content_type: str, origin: str, *, policy_ref: str,
                               evidence_group: str, observed_at: str, expires_at: str):
    rows = parse_document(body, content_type, origin)
    checksum = hashlib.sha256(body).hexdigest()
    observations = []
    for row in rows:
        signals = dict(row["signals"], label=row["label"], relation_hint=row["relation"], content_type=content_type)
        # Address country is NOT market_country. Scope of a search is NOT a claim.
        country = signals.get("claimed_country")
        countries = (country,) if isinstance(country, str) and len(country) == 2 and country.isupper() else ()
        class_claims = signals.get("claimed_vehicle_classes", [])
        classes = tuple(c for c in class_claims if c in CLASSES) if isinstance(class_claims, list) else ()
        source_type = signals.get("claimed_source_type", "unknown")
        if source_type not in SOURCE_TYPES:
            source_type = "unknown"
        observations.append(Observation(locator=row["locator"], kind=row["kind"], method="document_discovery",
            method_version=PARSER_VERSION, origin_locator=origin, evidence_group=evidence_group,
            observed_at=observed_at, expires_at=expires_at, policy_ref=policy_ref,
            countries=countries, classes=classes, source_type=source_type, signals=signals, body_sha256=checksum))
    return rows, observations, checksum


def inspect_document(store, body: bytes, content_type: str, origin: str, *, policy_ref: str,
                     evidence_group: str, observed_at: str, expires_at: str) -> dict:
    rows, observations, checksum = observations_from_document(body, content_type, origin, policy_ref=policy_ref,
        evidence_group=evidence_group, observed_at=observed_at, expires_at=expires_at)
    with store.transaction():
        store.put_artifact(body, expires_at=expires_at, policy_ref=policy_ref)
        ids = store.ingest_batch(observations)
    return {"candidate_ids": ids, "hints": len(rows), "body_sha256": checksum, "inventory_scraped": False,
            "document_has_no_hints": not rows, "coverage_ratio": None}


def expansion_tasks(task: dict, rows: list[dict]) -> list[dict]:
    """Visit identity is per plan/strategy/URL, not parent/depth: cycles converge."""
    result = {}
    for row in rows:
        url = row["locator"]
        if normalize_url(task["locator"]) == url:
            continue
        # Explicit parts links stay as evidence, not active vehicle discovery work.
        if row["signals"].get("link_context") == "parts":
            continue
        key = "link:" + digest([task["plan_id"], task["strategy"], url])
        payload = dict(task["payload"])
        payload.pop("planner_ordinal", None)
        payload.update(parent_task_key=task["task_key"], depth=task["payload"].get("depth", 0) + 1,
                       discovery_relation=row["relation"], scope_is_evidence=False)
        result[key] = dict(task_key=key, plan_id=task["plan_id"], stratum=task["stratum"], country=task["country"],
            classes=task["classes"], strategy=task["strategy"], kind="fetch", locator=url, priority=task["priority"],
            access_mode="public_web", payload=payload)
    return list(result.values())


def _retry_deadline(value: str | None, now: float) -> float:
    if value and value.strip().isdigit():
        return now + max(1, int(value))
    if value:
        try:
            return max(now + 1, parsedate_to_datetime(value).timestamp())
        except (TypeError, ValueError, OverflowError):
            pass
    return now + 60


def run_worker(store, policy: AccessPolicy, *, max_tasks: int = 20, max_seconds: float = 60,
               max_children: int = 100, max_depth: int = 3, fetcher=None) -> dict:
    if type(max_tasks) is not int or not 1 <= max_tasks <= 10000 or not 1 <= max_seconds <= 3600:
        raise ValueError("bounded task/time budget required")
    if type(max_children) is not int or not 0 <= max_children <= 5000 or type(max_depth) is not int or not 0 <= max_depth <= 20:
        raise ValueError("bounded child/depth budget required")
    started = time.monotonic()
    active_task = None
    policy_data = asdict(policy)
    policy_data["expires_at"] = policy.expires_at.isoformat()
    store.register_policy(policy.policy_ref, digest(policy_data))

    def before_request(url):
        if time.monotonic() - started >= max_seconds:
            raise Conflict("worker_time_budget_exhausted")
        with store.transaction():
            store.check_policy(policy.policy_ref)
            store._check_lease(active_task["task_key"], active_task["lease_token"], time.time())
            store.reserve_request(policy.policy_ref, url, max_requests=policy.max_requests,
                                  min_interval=network.required_interval(url))

    network = fetcher if fetcher is not None else SafeFetcher(policy, before_request=before_request, deadline_at=started + max_seconds)
    processed = 0
    outcomes = {}
    while processed < max_tasks and time.monotonic() - started < max_seconds:
        active_task = store.claim("local:discovery", lease_seconds=min(3600, max(120, max_seconds + 60)))
        if active_task is None:
            break
        task = active_task
        state, reason, retry_at = "done", "", 0
        retry_origin = task["locator"]
        try:
            if task["kind"] == "query":
                state, reason = "blocked", "human_or_approved_search_provider_required"
            elif task["access_mode"] in ("permission_required", "manual_review"):
                state, reason = "blocked", "method_requires_independent_access_review"
            elif urlsplit(task["locator"]).hostname not in policy.allowed_hosts:
                state, reason = "blocked", "host_not_in_explicit_policy"
            elif datetime.now(timezone.utc) >= policy.expires_at:
                state, reason = "blocked", "policy_expired"
            elif task["payload"].get("depth", 0) > max_depth:
                state, reason = "blocked", "depth_budget_requires_review"
            elif store.origin_ready_at(task["locator"]) > time.time():
                state, reason, retry_at = "retry", "shared_origin_cooldown", store.origin_ready_at(task["locator"])
            else:
                response = network.fetch(task["locator"])
                if response.status == 429 or response.status >= 500:
                    state, reason = "retry", f"http_{response.status}"
                    retry_origin = response.url
                    retry_at = _retry_deadline(response.headers.get("retry-after"), time.time())
                elif response.status != 200:
                    state, reason = "blocked", f"http_{response.status}_not_discovery_evidence"
                else:
                    seen_at = timestamp()
                    expiry = min(policy.expires_at, instant(seen_at) + timedelta(days=30))
                    rows, observations, checksum = observations_from_document(response.body,
                        response.headers.get("content-type", ""), response.url, policy_ref=policy.policy_ref,
                        evidence_group=task["payload"].get("evidence_group", "unknown"), observed_at=seen_at,
                        expires_at=timestamp(expiry.isoformat()))
                    children = expansion_tasks(task, rows)
                    # Already visited locators are deduplicated by immutable key, while every observation remains.
                    children = [t for t in children if not store.db.execute(
                        "SELECT 1 FROM tasks WHERE task_key=? UNION ALL SELECT 1 FROM deferred_frontier WHERE task_key=?", (t["task_key"], t["task_key"])).fetchone()]
                    room = max_children if task["payload"].get("depth", 0) < max_depth else 0
                    ready, deferred = children[:room], children[room:]
                    if deferred:
                        state, reason = "partial", "frontier_deferred_by_depth_or_child_budget"
                    elif not rows:
                        state, reason = "partial", "no_candidate_hints_not_proof_of_absence"
                    elif any(r["signals"].get("access_state") for r in rows):
                        state, reason = "partial", "access_or_activity_claim_requires_review"
                    with store.transaction():
                        store.check_policy(policy.policy_ref)
                        if datetime.now(timezone.utc) >= policy.expires_at:
                            raise Conflict("policy expired before result commit")
                        store.commit_result(task["task_key"], task["lease_token"], observations, status=state, reason=reason,
                            next_tasks=ready, deferred_tasks=deferred,
                            artifact=dict(body=response.body, expires_at=timestamp(expiry.isoformat()), policy_ref=policy.policy_ref))
                    outcomes[state] = outcomes.get(state, 0) + 1
                    processed += 1
                    continue
        except RateLimited as exc:
            state, reason, retry_at = "retry", "rate_limited", _retry_deadline(exc.retry_after, time.time())
            retry_origin = exc.url or task["locator"]
        except RobotsDenied:
            state, reason = "blocked", "robots_denied_or_unavailable"
        except PolicyError:
            state, reason = "blocked", "access_policy_denied"
        except (FetchLimitError, ParserLimitError):
            state, reason = "partial", "document_or_request_limit"
        except ParserError:
            state, reason = "partial", "unsupported_or_invalid_document"
        except TransportError:
            state, reason, retry_at = "retry", "transport_failure", time.time() + 60
        except Conflict as exc:
            if str(exc) == "shared_origin_cooldown":
                state, reason, retry_at = "retry", "shared_origin_cooldown", time.time() + max(1, policy.min_interval_seconds)
            else:
                state, reason = "blocked", "local_fence_retention_or_budget_guard"
        except ValueError:
            state, reason = "partial", "invalid_candidate_or_locator"
        try:
            if state == "retry" and reason in ("rate_limited", "http_429", "http_503"):
                store.cooldown_origin(retry_origin, retry_at)
            store.finish(task["task_key"], task["lease_token"], state, reason=reason, retry_at=retry_at)
        except Conflict:
            state = "fenced"
        outcomes[state] = outcomes.get(state, 0) + 1
        processed += 1
    return dict(tasks_processed=processed, outcomes=outcomes, elapsed_seconds=time.monotonic() - started,
                stop_reason="task_budget" if processed >= max_tasks else "deadline_or_no_eligible_work",
                deferred_frontier=store.deferred_count(), network_budgets=store.budget_status(),
                inventory_scraped=False, coverage_ratio=None)
