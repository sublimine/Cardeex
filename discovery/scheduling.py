"""Persistent discovery calendar. A tick plans work; workers govern all access.

Generation and cursor updates share the queue's SQLite transaction. Missed
windows remain debt and are never collapsed into a claim of continuous service.
Inspection opens SQLite read-only, including databases made before this module.
"""
from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from .model import Conflict, canonical_json, digest, instant, timestamp
from .planner import build_plan


def _schema(store):
    store.db.execute("""CREATE TABLE IF NOT EXISTS discovery_schedules (
        schedule_id TEXT PRIMARY KEY, spec TEXT NOT NULL, checksum TEXT NOT NULL,
        next_at REAL NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
        paused INTEGER NOT NULL DEFAULT 0, actor TEXT NOT NULL,
        last_tick INTEGER NOT NULL DEFAULT 0)""")
    store.db.execute("""CREATE TABLE IF NOT EXISTS discovery_generations (
        schedule_id TEXT NOT NULL REFERENCES discovery_schedules(schedule_id),
        due_at REAL NOT NULL, plan_id TEXT NOT NULL, tasks_generated INTEGER NOT NULL,
        generation_complete INTEGER NOT NULL, PRIMARY KEY(schedule_id,due_at))""")
    store.db.execute("""CREATE TABLE IF NOT EXISTS discovery_schedule_audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id TEXT NOT NULL,
        action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
        recorded_at TEXT NOT NULL)""")


def _actor(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError("bounded schedule actor required")


def _plan(spec, epoch, limit, offset=0):
    options = {"channels": spec["channels"]} if spec.get("channels") else {}
    if "catalogues" in spec:
        options["catalogues"] = spec["catalogues"]
    return build_plan(spec["profiles"], spec["localities"], epoch,
                      max_tasks=limit, offset=offset, **options)


def register_schedule(store, spec: dict, *, actor: str) -> dict:
    """Create an immutable schedule revision; a changed definition needs a new ID."""
    _actor(actor)
    required = {"schedule_id", "profiles", "localities", "starts_at", "interval_seconds"}
    if not isinstance(spec, dict) or not required <= spec.keys() or set(spec) - required - {"channels", "catalogues"}:
        raise ValueError("invalid discovery schedule fields")
    name, interval = spec["schedule_id"], spec["interval_seconds"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", name):
        raise ValueError("invalid schedule identity")
    if type(interval) is not int or not 1 <= interval <= 366 * 86400:
        raise ValueError("schedule interval outside supported bounds")
    spec = dict(spec, starts_at=timestamp(spec["starts_at"]))
    encoded = canonical_json(spec)
    if len(encoded.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("schedule definition exceeds byte limit")
    _plan(spec, "schedule-validation", 1)
    checksum = digest(spec)
    with store.transaction():
        _schema(store)
        old = store.db.execute("SELECT checksum FROM discovery_schedules WHERE schedule_id=?", (name,)).fetchone()
        if old and old[0] != checksum:
            raise Conflict("schedule revision is immutable; pause it and register a new identity")
        if old:
            return {"schedule_id": name, "created": False, "checksum": checksum}
        if store.db.execute("SELECT count(*) FROM discovery_schedules").fetchone()[0] >= 1000:
            raise ValueError("calendar schedule bound reached")
        store.db.execute("INSERT INTO discovery_schedules(schedule_id,spec,checksum,next_at,actor) VALUES (?,?,?,?,?)",
                         (name, encoded, checksum, instant(spec["starts_at"]).timestamp(), actor))
        store.db.execute("INSERT INTO discovery_schedule_audit(schedule_id,action,actor,reason,recorded_at) VALUES (?,?,?,?,?)",
                         (name, "registered", actor, "immutable_definition", timestamp()))
    return {"schedule_id": name, "created": True, "checksum": checksum}


def set_paused(store, schedule_id: str, paused: bool, *, actor: str, reason: str) -> dict:
    _actor(actor)
    if type(paused) is not bool or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("explicit bounded pause/review reason required")
    with store.transaction():
        _schema(store)
        row = store.db.execute("UPDATE discovery_schedules SET paused=? WHERE schedule_id=?", (int(paused), schedule_id))
        if row.rowcount != 1:
            raise Conflict("unknown discovery schedule")
        store.db.execute("INSERT INTO discovery_schedule_audit(schedule_id,action,actor,reason,recorded_at) VALUES (?,?,?,?,?)",
                         (schedule_id, "paused" if paused else "resumed", actor, reason, timestamp()))
    return {"schedule_id": schedule_id, "paused": paused, "existing_tasks_cancelled": False}


def tick(store, *, now: str | None = None, max_tasks: int = 1000, max_schedules: int = 20) -> dict:
    """Materialize bounded pages with exactly-once effects even across processes.

    Each selected schedule gets a share; existing pending work and observations
    survive refresh. A paused schedule stops future generation, not existing
    tasks; revocation remains the worker policy's separate responsibility.
    """
    at = instant(timestamp() if now is None else now).timestamp()
    if type(max_tasks) is not int or not 1 <= max_tasks <= 100000 or type(max_schedules) is not int or not 1 <= max_schedules <= 1000:
        raise ValueError("bounded tick required")
    generated, pages = 0, []
    with store.transaction():
        _schema(store)
        rows = store.db.execute("""SELECT * FROM discovery_schedules WHERE paused=0 AND next_at<=?
            ORDER BY last_tick,next_at,schedule_id LIMIT ?""", (at, min(max_schedules, max_tasks))).fetchall()
        for index, row in enumerate(rows):
            remaining = max_tasks - generated
            if not remaining:
                break
            spec = json.loads(row["spec"])
            if digest(spec) != row["checksum"]:
                raise Conflict("stored schedule definition checksum mismatch")
            due = timestamp(datetime.fromtimestamp(row["next_at"], timezone.utc).isoformat())
            epoch = "schedule:" + row["schedule_id"] + ":" + due
            page = _plan(spec, epoch, max(1, remaining // (len(rows) - index)), row["offset"])
            store.save_plan(page)
            count = len(page["tasks"])
            complete = page["generation_complete"]
            store.db.execute("""INSERT INTO discovery_generations VALUES (?,?,?,?,?)
                ON CONFLICT(schedule_id,due_at) DO UPDATE SET
                tasks_generated=discovery_generations.tasks_generated+excluded.tasks_generated,
                generation_complete=excluded.generation_complete""",
                (row["schedule_id"], row["next_at"], page["plan_id"], count, int(complete)))
            next_at = row["next_at"] + spec["interval_seconds"] if complete else row["next_at"]
            offset = 0 if complete else page["next_offset"]
            turn = store.db.execute("SELECT COALESCE(MAX(last_tick),0)+1 FROM discovery_schedules").fetchone()[0]
            store.db.execute("UPDATE discovery_schedules SET next_at=?,offset=?,last_tick=? WHERE schedule_id=?",
                             (next_at, offset, turn, row["schedule_id"]))
            store._event("discovery_generation_page", page["plan_id"], timestamp(now) if now else timestamp())
            generated += count
            pages.append({"schedule_id": row["schedule_id"], "plan_id": page["plan_id"], "due_at": due,
                          "tasks_generated": count, "generation_complete": complete,
                          "remaining_windows_due": max(0, int((at - next_at) // spec["interval_seconds"]) + 1)})
    return {"tasks_generated": generated, "pages": pages, "network_executed": False,
            "coverage_certified": False, "calendar_backlog_preserved": True}


def inspect_schedules(path: str | Path) -> dict:
    """Do not create a file, run migrations, expire evidence or seed defaults."""
    file = Path(path).resolve()
    result = {"schedules": [], "generations": [], "read_only": True}
    if not file.exists():
        return result
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_schedules'").fetchone():
            return result
        for row in db.execute("SELECT * FROM discovery_schedules ORDER BY next_at,schedule_id"):
            spec = json.loads(row["spec"])
            result["schedules"].append({"schedule_id": row["schedule_id"], "checksum": row["checksum"],
                "next_at": timestamp(datetime.fromtimestamp(row["next_at"], timezone.utc).isoformat()),
                "offset": row["offset"], "paused": bool(row["paused"]), "interval_seconds": spec["interval_seconds"],
                "countries": sorted(spec["profiles"])})
        result["generations"] = [dict(row) for row in db.execute(
            "SELECT * FROM discovery_generations ORDER BY due_at,schedule_id LIMIT 1000")]
        result["generation_history_truncated"] = db.execute("SELECT count(*) FROM discovery_generations").fetchone()[0] > 1000
    return result
