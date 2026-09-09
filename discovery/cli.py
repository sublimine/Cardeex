"""Operator CLI. Network is disabled except the explicit, policy-bound run command."""

import argparse
from itertools import islice
import json
import math
from pathlib import Path
import sqlite3
import sys

from .coverage import coverage_report
from .channels import validate_binding
from .engine import inspect_document, run_worker
from .estimation import estimate_stratum
from . import geography
from .handoff import handoff_records
from .model import Conflict, Observation, canonical_json, timestamp
from .planner import build_plan
from .profiles import load_profiles
from .scheduling import inspect_schedules, register_schedule, set_paused, tick
from .store import Store
from .technology import classify_technology
from .transport import AccessPolicy


def read_bytes(path: str, limit: int) -> bytes:
    with Path(path).open("rb") as handle:
        body = handle.read(limit + 1)
    if len(body) > limit:
        raise ValueError("input exceeds explicit byte limit")
    return body


def strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("non-finite JSON number")
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
    except RecursionError:
        raise ValueError("JSON nesting exceeds parser limit") from None


def read_json(path: str, limit: int = 8 * 1024 * 1024):
    return strict_json(read_bytes(path, limit).decode("utf-8-sig"))


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse otherwise echoes unrecognized arguments, including credentials.
        super().error("Invalid command or argument; use --help for the accepted interface.")


def _parser():
    parser = _SafeArgumentParser(prog="python -m discovery", description=__doc__)
    parser.add_argument("--db", default=".cardeex-local/discovery.sqlite")
    parser.add_argument("--profiles-dir", help="Explicit reviewed additional-country profile directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="Inspect researched country strategies without network")
    plan = commands.add_parser("plan", help="Persist a finite page of a versioned discovery frontier")
    plan.add_argument("--countries", default="ES,FR,DE,NL,BE,CH")
    places = plan.add_mutually_exclusive_group()
    places.add_argument("--localities", help="Reviewed JSON list of country/code/name/region/rural places")
    places.add_argument("--catalogue", help="Normalized geo-import JSON catalogue, including its provenance")
    plan.add_argument("--channels", help="Reviewed JSON mapping of strategy IDs to explicit channel bindings")
    plan.add_argument("--epoch", required=True, help="Explicit campaign/refresh window identity")
    plan.add_argument("--limit", type=int, default=1000)
    plan.add_argument("--offset", type=int, default=0)
    geo_import = commands.add_parser("geo-import", help="Validate local original catalogue bytes; output JSON, no database")
    geo_import.add_argument("--file", required=True)
    geo_import.add_argument("--metadata", required=True, help="JSON provenance and explicit import options")
    geo_import.add_argument("--format", choices=("json", "csv"), default="json")
    geo_resolve = commands.add_parser("geo-resolve", help="Resolve exact catalogue names/aliases; no database")
    geo_resolve.add_argument("--catalogue", required=True)
    geo_resolve.add_argument("--name", required=True)
    geo_resolve.add_argument("--region")
    technology = commands.add_parser("technology", help="Analyze a local document for technology hypotheses; no database")
    for name in ("file", "origin", "content-type"):
        technology.add_argument("--" + name, required=True)
    technology.add_argument("--headers", help="Inline JSON object of observed response headers, not a file path")
    estimate = commands.add_parser("estimate", help="Calculate a conditional estimate from reviewed local evidence; no database")
    estimate.add_argument("--file", required=True, help="JSON object containing records, independent_groups, stratum and now")
    schedule = commands.add_parser("schedule", help="Manage a frozen local discovery calendar; never execute network requests")
    schedule_commands = schedule.add_subparsers(dest="schedule_command", required=True)
    register = schedule_commands.add_parser("register", help="Register an immutable reviewed schedule definition")
    register.add_argument("--spec", required=True, help="JSON schedule with frozen profiles, geography and channel bindings")
    register.add_argument("--actor", required=True)
    schedule_commands.add_parser("show", help="Read calendar without creating a database or expiring evidence")
    for action in ("pause", "resume"):
        change = schedule_commands.add_parser(action)
        for name in ("id", "actor", "reason"):
            change.add_argument("--" + name, required=True)
    schedule_tick = schedule_commands.add_parser("tick", help="Enqueue a bounded page of due work; no network")
    schedule_tick.add_argument("--now")
    schedule_tick.add_argument("--max-tasks", type=int, default=1000)
    schedule_tick.add_argument("--max-schedules", type=int, default=20)
    coverage = commands.add_parser("coverage", help="Show unknowns, unplanned geography and work debt")
    coverage.add_argument("--countries", default="ES,FR,DE,NL,BE,CH")
    tasks = commands.add_parser("queue", help="Inspect work without executing any source")
    tasks.add_argument("--limit", type=int, default=100)
    tasks.add_argument("--status", choices=("pending", "running", "retry", "blocked", "partial", "done"))
    ingest = commands.add_parser("ingest", help="Atomic bounded JSONL Observation import")
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--max-records", type=int, default=10000)
    inspect = commands.add_parser("inspect-file", help="Pure document replay; no network")
    inspect.add_argument("--file", required=True)
    inspect.add_argument("--origin", required=True)
    inspect.add_argument("--content-type", required=True)
    inspect.add_argument("--policy-ref", required=True)
    inspect.add_argument("--evidence-group", required=True)
    inspect.add_argument("--observed-at", required=True, help="Original source observation time, not replay time")
    inspect.add_argument("--expires-at", required=True, help="Approved retention expiry")
    channel = commands.add_parser("inspect-channel", help="Replay a local channel response under its reviewed binding; no network")
    for name in ("file", "binding", "origin", "content-type", "policy-ref", "observed-at", "expires-at"):
        channel.add_argument("--" + name, required=True)
    resume_channel = commands.add_parser("resume-channel", help="Resume preserved channel page-window debt after explicit review; no network")
    for name in ("task", "binding", "actor", "reason"):
        resume_channel.add_argument("--" + name, required=True)
    run = commands.add_parser("run", help="Execute bounded discovery using an explicit approved host policy")
    run.add_argument("--policy", required=True)
    run.add_argument("--max-tasks", type=int, default=20)
    run.add_argument("--max-seconds", type=float, default=60)
    run.add_argument("--max-children", type=int, default=100)
    run.add_argument("--max-depth", type=int, default=3)
    candidate = commands.add_parser("candidate", help="Inspect evidence and decision history")
    candidate.add_argument("candidate_id")
    review = commands.add_parser("review", help="Review claim, never automatically admit inventory")
    review.add_argument("--candidate", required=True)
    review.add_argument("--status", choices=("accepted", "duplicate_candidate", "rejected", "needs_evidence"), required=True)
    review.add_argument("--revision", type=int, required=True)
    review.add_argument("--actor", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--target")
    relation = commands.add_parser("relate", help="Record typed evidence relation without entity merge")
    for name in ("source", "target", "kind", "evidence", "actor"):
        relation.add_argument("--" + name, required=True)
    commands.add_parser("export", help="Stream canonical candidates + claims/relations as JSONL")
    commands.add_parser("expire", help="Apply recorded retention deadlines locally")
    suppress = commands.add_parser("suppress", help="Erase candidate claims/raw and prevent ordinary replay")
    for name in ("candidate", "actor", "reason"):
        suppress.add_argument("--" + name, required=True)
    requeue = commands.add_parser("requeue", help="Reconsider one blocked/partial task after review")
    requeue.add_argument("--task", required=True)
    requeue.add_argument("--reason", required=True)
    release = commands.add_parser("release-frontier", help="Release preserved deferred link work in bounded batches")
    release.add_argument("--limit", type=int, default=100)
    commands.add_parser("doctor", help="Check database integrity and local runtime; no network")
    revoke = commands.add_parser("revoke-policy", help="Revoke a registered network policy, including running workers")
    for name in ("policy-ref", "actor", "reason"):
        revoke.add_argument("--" + name, required=True)
    return parser


def _selected(profiles, countries):
    selected = countries.split(",")
    if not selected or len(set(selected)) != len(selected) or any(c not in profiles for c in selected):
        raise ValueError("unknown or duplicate country; install and review its profile first")
    return {c: profiles[c] for c in selected}


def _ingest(store, path: str, max_records: int):
    if not 1 <= max_records <= 100000:
        raise ValueError("bounded import count required")
    processed, ids = 0, set()
    with store.transaction(), Path(path).open("rb") as handle:
        total_bytes = 0
        while True:
            line = handle.readline(65537)
            if not line:
                break
            total_bytes += len(line)
            if len(line) > 65536 or total_bytes > 64 * 1024 * 1024:
                raise ValueError("import byte bound exceeded; transaction rolled back")
            if not line.strip():
                continue
            processed += 1
            if processed > max_records:
                raise ValueError("import record bound exceeded; transaction rolled back")
            data = strict_json(line.decode("utf-8-sig"))
            ids.add(store.ingest(Observation(**data)))
    return {"observations_processed": processed, "distinct_candidate_ids": len(ids), "inventory_scraped": False}


def _execute(args, store, profiles):
    if args.command == "plan":
        localities = read_json(args.localities) if args.localities else []
        options = {}
        if args.channels:
            mapping = read_json(args.channels)
            if not isinstance(mapping, dict):
                raise ValueError("channels require a strategy-to-binding mapping")
            options["channels"] = {key: validate_binding(value) for key, value in mapping.items()}
        if args.catalogue:
            options["catalogues"] = [geography.validate_catalogue(read_json(args.catalogue, geography.MAX_SERIALIZED_BYTES))]
        plan = build_plan(_selected(profiles, args.countries), localities, args.epoch,
                          max_tasks=args.limit, offset=args.offset, **options)
        store.save_plan(plan)
        return {**{k: v for k, v in plan.items() if k != "tasks"}, "tasks_enqueued": len(plan["tasks"]), "network_executed": False}
    if args.command == "schedule":
        if args.schedule_command == "register":
            return register_schedule(store, read_json(args.spec), actor=args.actor)
        if args.schedule_command in ("pause", "resume"):
            return set_paused(store, args.id, args.schedule_command == "pause", actor=args.actor, reason=args.reason)
        if args.schedule_command == "tick":
            return tick(store, now=args.now, max_tasks=args.max_tasks, max_schedules=args.max_schedules)
    if args.command == "coverage":
        with store.transaction():
            result = coverage_report(store, _selected(profiles, args.countries), now=timestamp())
            result["deferred_frontier"] = store.deferred_count()
            result["network_budgets"] = store.budget_status()
            return result
    if args.command == "queue":
        if not 1 <= args.limit <= 100000:
            raise ValueError("bounded queue page required")
        rows = (t for t in store.iter_tasks() if args.status is None or t["status"] == args.status)
        return {"tasks": list(islice(rows, args.limit)), "page_is_not_full_frontier": True, "deferred_frontier": store.deferred_count()}
    if args.command == "ingest":
        return _ingest(store, args.file, args.max_records)
    if args.command == "inspect-file":
        observed = timestamp(args.observed_at)
        expires = timestamp(args.expires_at)
        return inspect_document(store, read_bytes(args.file, 2 * 1024 * 1024), args.content_type, args.origin,
            policy_ref=args.policy_ref, evidence_group=args.evidence_group, observed_at=observed, expires_at=expires)
    if args.command == "inspect-channel":
        from .engine import inspect_channel
        return inspect_channel(store, read_bytes(args.file, 2 * 1024 * 1024), args.content_type, args.origin,
                               binding=args.binding_data, policy_ref=args.policy_ref,
                               observed_at=timestamp(args.observed_at), expires_at=timestamp(args.expires_at))
    if args.command == "resume-channel":
        from .engine import resume_channel
        return resume_channel(store, args.task, args.binding_data, actor=args.actor, reason=args.reason)
    if args.command == "run":
        policy = AccessPolicy(**read_json(args.policy, 64 * 1024))
        return run_worker(store, policy, max_tasks=args.max_tasks, max_seconds=args.max_seconds,
                          max_children=args.max_children, max_depth=args.max_depth)
    if args.command == "candidate":
        return store.candidate(args.candidate_id)
    if args.command == "review":
        return store.decide(args.candidate, args.status, actor=args.actor, reason=args.reason,
                            expected_revision=args.revision, target_id=args.target)
    if args.command == "relate":
        store.relate(args.source, args.target, args.kind, evidence_id=args.evidence, actor=args.actor)
        return {"relation_recorded": True, "entities_merged": False}
    if args.command == "suppress":
        store.suppress(args.candidate, actor=args.actor, reason=args.reason)
        return {"suppressed": args.candidate, "ordinary_reimport_blocked": True, "external_exports_not_managed": True}
    if args.command == "expire":
        return store.expire()
    if args.command == "requeue":
        store.requeue(args.task, reason=args.reason)
        return {"requeued": args.task, "policy_still_required": True}
    if args.command == "release-frontier":
        return {"released": store.release_deferred(args.limit), "remaining": store.deferred_count()}
    if args.command == "doctor":
        return {"sqlite_version": sqlite3.sqlite_version,
                "integrity_check": [r[0] for r in store.db.execute("PRAGMA integrity_check")],
                "foreign_key_errors": [list(r) for r in store.db.execute("PRAGMA foreign_key_check")],
                "journal_mode": store.db.execute("PRAGMA journal_mode").fetchone()[0],
                "storage_role": "local_discovery_workbench_not_production_control", "paid_services_required": False}
    if args.command == "revoke-policy":
        store.revoke_policy(args.policy_ref, actor=args.actor, reason=args.reason)
        return {"policy_revoked": args.policy_ref}
    raise ValueError("unsupported command")


def _execute_pure(args):
    if args.command == "geo-import":
        return geography.load_catalogue(read_bytes(args.file, geography.MAX_BODY_BYTES),
                                        read_json(args.metadata, 64 * 1024), args.format)
    if args.command == "geo-resolve":
        return geography.resolve_place(read_json(args.catalogue, geography.MAX_SERIALIZED_BYTES), args.name, args.region)
    if args.command == "technology":
        if args.headers is not None and len(args.headers.encode("utf-8")) > 64 * 1024:
            raise ValueError("observed headers exceed byte limit")
        headers = strict_json(args.headers) if args.headers is not None else None
        if headers is not None and not isinstance(headers, dict):
            raise ValueError("observed headers require a JSON object")
        return classify_technology(read_bytes(args.file, 2 * 1024 * 1024), args.content_type, args.origin, headers)
    if args.command == "estimate":
        data = read_json(args.file, 64 * 1024 * 1024)
        if not isinstance(data, dict) or set(data) != {"records", "independent_groups", "stratum", "now"}:
            raise ValueError("estimate requires exactly records, independent_groups, stratum and now")
        return estimate_stratum(data["records"], independent_groups=data["independent_groups"],
                                stratum=data["stratum"], now=data["now"])
    if args.command == "schedule" and args.schedule_command == "show":
        return inspect_schedules(args.db)
    raise ValueError("unsupported pure command")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in ("geo-import", "geo-resolve", "technology", "estimate") or (
                args.command == "schedule" and args.schedule_command == "show"):
            print(canonical_json(_execute_pure(args)))
            return 0
        if args.command in ("inspect-channel", "resume-channel"):
            args.binding_data = validate_binding(read_json(args.binding, 64 * 1024))
            if args.command == "inspect-channel" and args.binding_data["assessment_ref"] != args.policy_ref:
                raise ValueError("binding assessment and replay policy must match")
        profiles = load_profiles(args.profiles_dir)
        if args.command == "profiles":
            print(canonical_json(profiles))
            return 0
        with Store(args.db) as store:
            if args.command != "expire":
                store.expire()
            if args.command == "export":
                for record in handoff_records(store):
                    print(canonical_json(record))
            else:
                result = _execute(args, store, profiles)
                print(canonical_json(result))
                if args.command == "doctor" and (result["integrity_check"] != ["ok"] or result["foreign_key_errors"]):
                    return 1
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, ImportError) as exc:
        # Raw URLs, document strings and validators can contain sensitive source input.
        print(canonical_json({"error": type(exc).__name__, "operation": args.command,
                              "message": "Input, policy, contract or local storage rejected; no inventory completeness is implied."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
