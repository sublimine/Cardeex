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

from .adapters import PARSER_VERSION, ParserError, ParserLimitError, parse_document, discovery_exclusion
from .model import CLASSES, SOURCE_TYPES, Conflict, Observation, digest, instant, timestamp
from .transport import (AccessPolicy, FetchLimitError, PolicyError, RateLimited,
                        RobotsDenied, SafeFetcher, TransportError, normalize_url)
from .channels import CHANNEL_VERSION, ChannelError, ChannelLimitError, validate_binding, request_url, parse_response
from .technology import classify_technology
from .channel_runtime import inspect_channel, resume_channel


def observations_from_document(body: bytes, content_type: str, origin: str, *, policy_ref: str,
                               evidence_group: str, observed_at: str, expires_at: str, headers=None):
    rows = parse_document(body, content_type, origin)
    technology = classify_technology(body, content_type, origin, headers)
    if technology["detections"] or technology["inventory_surfaces"] or technology["dynamic_rendering_required"] is True:
        rows.append(dict(locator=origin, kind="source", relation="technology_observation", label="",
                         signals={"technology": technology, "claim_status": "unverified",
                                  "evidence_selector": "document", "assertion_method": "technology_signatures"}))
        for surface in technology["inventory_surfaces"]:
            rows.append(dict(locator=surface["locator"], kind="source", relation="inventory_surface", label="",
                signals={"claim_status": "unverified", "requires_access_review": True,
                         "evidence_selector": surface["selector"], "assertion_method": surface["rule"]}))
    return observations_from_rows(rows, body, content_type, origin, policy_ref=policy_ref,
        evidence_group=evidence_group, observed_at=observed_at, expires_at=expires_at)


def observations_from_rows(rows, body: bytes, content_type: str, origin: str, *, policy_ref: str,
                           evidence_group: str, observed_at: str, expires_at: str, method_version=PARSER_VERSION):
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
            method_version=method_version, origin_locator=origin, evidence_group=evidence_group,
            observed_at=observed_at, expires_at=expires_at, policy_ref=policy_ref,
            countries=countries, classes=classes, source_type=source_type, signals=signals, body_sha256=checksum))
    return rows, observations, checksum


def inspect_document(store, body: bytes, content_type: str, origin: str, *, policy_ref: str,
                     evidence_group: str, observed_at: str, expires_at: str) -> dict:
    rows, observations, checksum = observations_from_document(body, content_type, origin, policy_ref=policy_ref,
        evidence_group=evidence_group, observed_at=observed_at, expires_at=expires_at)
    with store.transaction():
        store.check_replay_policy(policy_ref)
        store.put_artifact(body, expires_at=expires_at, policy_ref=policy_ref)
        ids = store.ingest_batch(observations)
    return {"candidate_ids": ids, "hints": len(rows), "body_sha256": checksum, "inventory_scraped": False,
            "document_has_no_hints": not rows, "coverage_ratio": None}


def expansion_tasks(task: dict, rows: list[dict]) -> list[dict]:
    """Visit identity is per plan/strategy/URL, not parent/depth: cycles converge."""
    result = {}
    review_required = {row["locator"] for row in rows if row["signals"].get("requires_access_review")}
    for row in rows:
        url = row["locator"]
        if normalize_url(task["locator"]) == url:
            continue
        if url in review_required or discovery_exclusion(url) or row["signals"].get("discovery_fetch_allowed") is False:
            continue
        # Explicit parts links stay as evidence, not active vehicle discovery work.
        if row["signals"].get("link_context") == "parts":
            continue
        key = "link:" + digest([task["plan_id"], task["strategy"], url])
        payload = dict(task["payload"])
        payload.pop("planner_ordinal", None)
        for channel_field in ("channel", "channel_checksum", "channel_cursor", "channel_page", "channel_root_task"):
            payload.pop(channel_field, None)
        # A newly fetched site is its own evidence source, not corroboration by
        # the search engine/directory through which it was first encountered.
        payload["evidence_group"] = "web:" + urlsplit(url).hostname
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
    active_fetch_url = None
    policy_data = asdict(policy)
    policy_data["expires_at"] = policy.expires_at.isoformat()
    store.register_policy(policy.policy_ref, digest(policy_data))

    def before_request(url):
        if discovery_exclusion(url):
            raise PolicyError("request exceeds discovery boundary")
        if (active_task["payload"].get("channel") and network.request_phase != "robots"
                and normalize_url(url) != active_fetch_url):
            raise PolicyError("redirect changes assessed channel request")
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
            binding = task["payload"].get("channel")
            fetch_url = task["locator"]
            if binding is not None:
                binding = validate_binding(binding)
                if digest(binding) != task["payload"].get("channel_checksum"):
                    raise PolicyError("channel configuration checksum mismatch")
                if binding["assessment_ref"] != policy.policy_ref or task["country"] not in binding["countries"] or not set(task["classes"]) <= set(binding["vehicle_classes"]):
                    raise PolicyError("channel assessment or scope mismatch")
                if (task["kind"] == "query") != (binding["adapter"] == "searxng_json"):
                    raise PolicyError("channel operation mismatch")
                page_number = task["payload"].get("channel_page", 1)
                if type(page_number) is not int or not 1 <= page_number <= binding["max_pages"]:
                    raise PolicyError("channel page limit requires a revised plan")
                fetch_url = request_url(binding, dict(task, query=task["locator"]), task["payload"].get("channel_cursor"))
            if task["kind"] == "query" and binding is None:
                state, reason = "blocked", "human_or_approved_search_provider_required"
            elif task["access_mode"] == "permission_required" or (task["access_mode"] == "manual_review" and binding is None):
                state, reason = "blocked", "method_requires_independent_access_review"
            elif discovery_exclusion(fetch_url):
                state, reason = "blocked", "outside_discovery_boundary"
            elif urlsplit(fetch_url).hostname not in policy.allowed_hosts:
                state, reason = "blocked", "host_not_in_explicit_policy"
            elif datetime.now(timezone.utc) >= policy.expires_at:
                state, reason = "blocked", "policy_expired"
            elif task["payload"].get("depth", 0) > max_depth:
                state, reason = "blocked", "depth_budget_requires_review"
            elif store.origin_ready_at(fetch_url) > time.time():
                state, reason, retry_at = "retry", "shared_origin_cooldown", store.origin_ready_at(fetch_url)
            else:
                retry_origin = fetch_url
                active_fetch_url = normalize_url(fetch_url)
                response = network.fetch(fetch_url)
                if response.status == 429 or response.status >= 500:
                    state, reason = "retry", f"http_{response.status}"
                    retry_origin = response.url
                    retry_at = _retry_deadline(response.headers.get("retry-after"), time.time())
                elif response.status != 200:
                    state, reason = "blocked", f"http_{response.status}_not_discovery_evidence"
                else:
                    seen_at = timestamp()
                    expiry = min(policy.expires_at, instant(seen_at) + timedelta(days=30))
                    metadata = dict(policy_ref=policy.policy_ref,
                        evidence_group=task["payload"].get("evidence_group", "unknown"),
                        observed_at=seen_at, expires_at=timestamp(expiry.isoformat()))
                    channel_result = None
                    if binding:
                        try:
                            channel_result = parse_response(binding, response.body, response.headers.get("content-type", ""), response.url)
                            rows, observations, checksum = observations_from_rows(channel_result["rows"], response.body,
                                response.headers.get("content-type", ""), response.url, method_version=CHANNEL_VERSION, **metadata)
                        except (ChannelError, ValueError):
                            # Retained unparseable responses are diagnostic evidence,
                            # never an empty source result or a loss of the worker.
                            rows, observations = [], []
                            checksum = hashlib.sha256(response.body).hexdigest()
                            channel_result = dict(rows=None, continuation=None, complete=None,
                                                  warnings=["source_format_or_candidate_validation_failed"])
                    else:
                        rows, observations, checksum = observations_from_document(response.body,
                            response.headers.get("content-type", ""), response.url, headers=response.headers, **metadata)
                    children = expansion_tasks(dict(task, locator=response.url), rows)
                    # Already visited locators are deduplicated by immutable key, while every observation remains.
                    children = [t for t in children if not store.db.execute(
                        "SELECT 1 FROM tasks WHERE task_key=? UNION ALL SELECT 1 FROM deferred_frontier WHERE task_key=?", (t["task_key"], t["task_key"])).fetchone()]
                    room = max_children if task["payload"].get("depth", 0) < max_depth else 0
                    ready, deferred = children[:room], children[room:]
                    if channel_result and channel_result["continuation"] is not None:
                        continuation = channel_result["continuation"]
                        root_key = task["payload"].get("channel_root_task", task["task_key"])
                        child_key = "channel:" + digest([task["plan_id"], root_key, task["payload"]["channel_checksum"], continuation])
                        next_url = request_url(binding, dict(task, query=task["locator"]), continuation)
                        repeated = next_url == fetch_url or store.db.execute(
                            "SELECT 1 FROM tasks WHERE task_key=? UNION ALL SELECT 1 FROM deferred_frontier WHERE task_key=?",
                            (child_key, child_key)).fetchone()
                        if repeated:
                            state, reason = "partial", "channel_repeated_cursor"
                        else:
                            next_payload = dict(task["payload"], channel_cursor=continuation,
                                channel_page=page_number + 1, channel_root_task=root_key, parent_task_key=task["task_key"])
                            next_payload.pop("planner_ordinal", None)
                            next_task = dict(task, task_key=child_key, payload=next_payload)
                            for key in ("lease_token", "lease_until", "attempts"):
                                next_task.pop(key, None)
                            if page_number >= binding["max_pages"]:
                                deferred.append(next_task)
                            else:
                                ready.append(next_task)
                    if deferred:
                        state, reason = "partial", "frontier_deferred_by_depth_or_child_budget"
                    elif channel_result and (channel_result["complete"] is not True or channel_result["warnings"]):
                        state, reason = "partial", "channel_limits_or_uncertainty"
                    elif not rows:
                        if channel_result is None or channel_result["complete"] is not True:
                            state, reason = "partial", "no_candidate_hints_not_proof_of_absence"
                    elif any(r["signals"].get("access_state") for r in rows):
                        state, reason = "partial", "access_or_activity_claim_requires_review"
                    elif any(r["signals"].get("requires_access_review") for r in rows):
                        state, reason = "partial", "surface_requires_access_review"
                    elif any(r["signals"].get("detail_hints_truncated") or r["signals"].get("technology", {}).get("dynamic_rendering_required") is True for r in rows):
                        state, reason = "partial", "document_hints_truncated_or_dynamic"
                    with store.transaction():
                        store.check_policy(policy.policy_ref)
                        if datetime.now(timezone.utc) >= policy.expires_at:
                            raise Conflict("policy expired before result commit")
                        store.commit_result(task["task_key"], task["lease_token"], observations, status=state, reason=reason,
                            next_tasks=ready, deferred_tasks=deferred,
                            artifact=dict(body=response.body, expires_at=timestamp(expiry.isoformat()), policy_ref=policy.policy_ref))
                        if channel_result is not None:
                            store.record_channel_page(task, response.url, channel_result, seen_at, checksum,
                                                      expires_at=metadata["expires_at"])
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
        except (FetchLimitError, ParserLimitError, ChannelLimitError):
            state, reason = "partial", "document_or_request_limit"
        except (ParserError, ChannelError):
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
