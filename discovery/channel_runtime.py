"""Local source replay and audited continuation windows; no network authority."""
import json
from urllib.parse import urlsplit

from .channels import CHANNEL_VERSION, parse_response, validate_binding
from .model import Conflict, canonical_json, digest, timestamp


def inspect_channel(store, body, content_type, origin, *, binding, policy_ref, observed_at, expires_at):
    from .engine import observations_from_rows
    binding = validate_binding(binding)
    observed_at, expires_at = timestamp(observed_at), timestamp(expires_at)
    if binding["assessment_ref"] != policy_ref:
        raise ValueError("channel assessment mismatch")
    result = parse_response(binding, body, content_type, origin)
    raw_retained = binding["adapter"] != "sirene_csv"
    if not raw_retained:
        # Original CSV may include protected persons even when output is filtered.
        # Keep only allowed projections and checksum, never the whole private slice.
        for row in result["rows"]:
            row["signals"]["raw_withheld"] = "sirene_diffusion_and_personal_fields"
    _, observations, checksum = observations_from_rows(result["rows"], body, content_type, origin,
        policy_ref=policy_ref, evidence_group="channel:" + binding["adapter"] + ":" + urlsplit(binding["endpoint"]).hostname,
        observed_at=observed_at, expires_at=expires_at, method_version=CHANNEL_VERSION)
    task = dict(task_key="import:" + digest([origin, checksum, observed_at, digest(binding)]),
                plan_id="local_source_import", payload={"channel": binding})
    with store.transaction():
        store.check_replay_policy(policy_ref)
        if raw_retained:
            store.put_artifact(body, expires_at=expires_at, policy_ref=policy_ref)
        ids = store.ingest_batch(observations)
        page_id = store.record_channel_page(task, origin, result, observed_at, checksum,
                                            expires_at=expires_at, raw_retained=raw_retained)
    return dict(candidate_ids=ids, page_id=page_id, body_sha256=checksum, raw_retained=raw_retained,
        complete=result["complete"], continuation=result["continuation"], warnings=result["warnings"],
        network_executed=False, inventory_scraped=False, coverage_ratio=None)


def resume_channel(store, task_key, binding, *, actor, reason):
    """Admit one further page window, preserving the cursor and request budgets.

    Endpoint, source parameters and scope cannot change in a continuation. A new
    assessment/page window is explicit and recorded, never a mutation of a plan.
    """
    binding = validate_binding(binding)
    for value, limit in ((actor, 256), (reason, 2000)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError("explicit bounded continuation review required")
    binding_hash = digest(binding)
    with store.transaction():
        receipt = store.db.execute("SELECT * FROM channel_resumptions WHERE original_task_key=?", (task_key,)).fetchone()
        if receipt:
            if receipt["binding_sha256"] != binding_hash:
                raise Conflict("continuation already resumed with a different review")
            return dict(task_key=receipt["task_key"], created=False, policy_still_required=True)
        row = store.db.execute("SELECT payload FROM deferred_frontier WHERE task_key=?", (task_key,)).fetchone()
        deferred = row is not None
        if not row:
            row = store.db.execute("SELECT payload FROM tasks WHERE task_key=? AND status IN ('blocked','partial')", (task_key,)).fetchone()
        if not row:
            raise Conflict("no reviewable channel continuation")
        original = json.loads(row[0])
        previous = validate_binding(original["payload"].get("channel"))
        for key in ("adapter", "endpoint", "parameters", "countries", "vehicle_classes"):
            if binding[key] != previous[key]:
                raise Conflict("continuation cannot change source parameters or scope")
        if (original["payload"].get("channel_checksum") != digest(previous)
                or original["payload"].get("channel_cursor") is None
                or original["payload"].get("channel_page", 0) <= previous["max_pages"]):
            raise Conflict("only preserved page-window debt can be resumed")
        new_key = "resume:" + digest([task_key, binding_hash])
        payload = dict(original["payload"], channel=binding, channel_checksum=binding_hash,
                       channel_page=1, channel_root_task=new_key, planner_ordinal=0,
                       parent_task_key=None, supersedes_task_key=task_key)
        new_task = {key: original[key] for key in ("stratum", "country", "classes", "strategy", "kind", "locator", "priority", "access_mode")}
        new_task.update(task_key=new_key, plan_id="plan:" + digest([new_key]), payload=payload)
        previous_plans = [p for p in store.list_plans() if p["plan_id"] == original["plan_id"]]
        previous_plan = previous_plans[0] if previous_plans else {}
        plan = {**previous_plan, "plan_id": new_task["plan_id"], "parent_plan_id": original["plan_id"],
            "epoch": new_key, "offset": 0, "next_offset": None, "total_tasks": 1, "generation_complete": True,
            "channels": {original["strategy"]: binding}, "channels_sha256": digest({original["strategy"]: binding}),
            "tasks": [new_task]}
        store.save_plan(plan)
        receipt_id = digest([task_key, new_key])
        audit = dict(actor=actor, reason=reason, recorded_at=timestamp(), original_task=original,
                     old_binding_sha256=digest(previous), new_binding_sha256=binding_hash)
        store.db.execute("INSERT INTO channel_resumptions VALUES (?,?,?,?,?)",
                         (receipt_id, task_key, new_key, binding_hash, canonical_json(audit)))
        if deferred:
            store.db.execute("DELETE FROM deferred_frontier WHERE task_key=?", (task_key,))
        else:
            store.db.execute("UPDATE tasks SET status='cancelled',reason='superseded_by_reviewed_channel_revision' WHERE task_key=?", (task_key,))
        store._event("channel_window_resumed", new_key, timestamp())
    return dict(task_key=new_key, created=True, policy_still_required=True)
