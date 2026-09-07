"""Persistent fair frontier, leases and network budget accounting."""

import json
import math
import time
from urllib.parse import urlsplit

from .model import Conflict, canonical_json, new_id, timestamp


class QueueMixin:
    def _init_queue(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
          task_key TEXT PRIMARY KEY, stratum TEXT NOT NULL, priority INTEGER NOT NULL,
          status TEXT NOT NULL, not_before REAL NOT NULL DEFAULT 0, lease_until REAL,
          lease_token TEXT, worker TEXT, attempts INTEGER NOT NULL DEFAULT 0,
          reason TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS tasks_ready ON tasks(status,not_before,stratum,priority);
        CREATE TABLE IF NOT EXISTS strata (stratum TEXT PRIMARY KEY, last_claim INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS queue_clock (id INTEGER PRIMARY KEY CHECK(id=1), seq INTEGER NOT NULL);
        INSERT OR IGNORE INTO queue_clock VALUES (1,0);
        CREATE TABLE IF NOT EXISTS budgets (policy_ref TEXT PRIMARY KEY, spent INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS origins (origin TEXT PRIMARY KEY, next_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS deferred_frontier (task_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS policies (policy_ref TEXT PRIMARY KEY, checksum TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS policy_audit (id TEXT PRIMARY KEY, policy_ref TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL);
        """)

    def enqueue(self, task: dict):
        required = {"task_key", "plan_id", "stratum", "country", "classes", "strategy", "kind", "locator", "priority", "access_mode", "payload"}
        if not isinstance(task, dict) or not required <= set(task) or type(task["priority"]) is not int or not 0 <= task["priority"] <= 100:
            raise ValueError("invalid finite discovery task")
        if task["kind"] not in ("fetch", "query") or task["access_mode"] not in ("open_data", "public_web", "manual_review", "permission_required"):
            raise ValueError("invalid task operation/access")
        encoded = canonical_json(task)
        if len(encoded) > 32768 or not task["task_key"] or not task["stratum"]:
            raise ValueError("task metadata too large or missing identity")
        with self.transaction():
            old = self.db.execute("SELECT payload FROM tasks WHERE task_key=?", (task["task_key"],)).fetchone()
            if old and old[0] != encoded:
                raise Conflict("task key reused with different immutable plan")
            self.db.execute("INSERT OR IGNORE INTO strata(stratum) VALUES (?)", (task["stratum"],))
            self.db.execute("INSERT OR IGNORE INTO tasks(task_key,stratum,priority,status,payload,created_at) VALUES (?,?,?,'pending',?,?)",
                            (task["task_key"], task["stratum"], task["priority"], encoded, timestamp()))

    def claim(self, worker: str, *, now: float | None = None, lease_seconds: float = 60) -> dict | None:
        now = time.time() if now is None else now
        if not worker.strip() or not math.isfinite(now) or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid worker clock or bounded lease")
        with self.transaction():
            self.db.execute("UPDATE tasks SET status='retry',not_before=?,lease_token=NULL,lease_until=NULL,reason='lease_expired' WHERE status='running' AND lease_until<=?", (now, now))
            row = self.db.execute("""SELECT t.* FROM tasks t JOIN strata s USING(stratum)
              WHERE t.status IN ('pending','retry') AND t.not_before<=?
              ORDER BY s.last_claim ASC,t.created_at ASC,t.priority DESC,t.task_key ASC LIMIT 1""", (now,)).fetchone()
            if not row:
                return None
            seq = self.db.execute("UPDATE queue_clock SET seq=seq+1 WHERE id=1 RETURNING seq").fetchone()[0]
            token = new_id()
            self.db.execute("UPDATE strata SET last_claim=? WHERE stratum=?", (seq, row["stratum"]))
            self.db.execute("UPDATE tasks SET status='running',lease_token=?,lease_until=?,worker=?,attempts=attempts+1 WHERE task_key=?",
                            (token, now + lease_seconds, worker, row["task_key"]))
            return dict(json.loads(row["payload"]), lease_token=token, lease_until=now + lease_seconds, attempts=row["attempts"] + 1)

    def _check_lease(self, task_key: str, token: str, now: float):
        if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now):
            raise ValueError("finite worker clock required")
        row = self.db.execute("SELECT status,lease_token,lease_until FROM tasks WHERE task_key=?", (task_key,)).fetchone()
        if not row or row[0] != "running" or row[1] != token or row[2] <= now:
            raise Conflict("expired or superseded worker fence")

    def finish(self, task_key: str, token: str, status: str, *, reason: str = "", now: float | None = None, retry_at: float = 0):
        now = time.time() if now is None else now
        if status not in ("done", "retry", "blocked", "partial") or len(reason) > 256:
            raise ValueError("invalid task outcome")
        if status in ("blocked", "partial", "retry") and not reason:
            raise ValueError("non-success requires reason")
        if not math.isfinite(retry_at) or (status == "retry" and retry_at <= now):
            raise ValueError("retry requires a future finite deadline")
        with self.transaction():
            self._check_lease(task_key, token, now)
            self.db.execute("UPDATE tasks SET status=?,reason=?,not_before=?,lease_until=NULL,lease_token=NULL WHERE task_key=?",
                            (status, reason, retry_at, task_key))
            self._event("task_" + status, task_key, timestamp())

    def commit_result(self, task_key: str, token: str, observations, *, now: float | None = None,
                      status: str = "done", reason: str = "", next_tasks=(), deferred_tasks=(), artifact=None):
        """Fencing covers evidence, expansion and completion in the SAME transaction."""
        now = time.time() if now is None else now
        with self.transaction():
            self._check_lease(task_key, token, now)
            if artifact is not None:
                self.put_artifact(**artifact)
            ids = self.ingest_batch(observations)
            for task in next_tasks:
                self.enqueue(task)
            for task in deferred_tasks:
                self.db.execute("INSERT OR IGNORE INTO deferred_frontier VALUES (?,?)", (task["task_key"], canonical_json(task)))
            self.finish(task_key, token, status, reason=reason, now=now)
            return ids

    def iter_tasks(self):
        for row in self.db.execute("SELECT * FROM tasks ORDER BY task_key"):
            yield dict(json.loads(row["payload"]), status=row["status"], reason=row["reason"],
                       attempts=row["attempts"], not_before=row["not_before"], lease_until=row["lease_until"], created_at=row["created_at"])

    def release_deferred(self, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 100000:
            raise ValueError("bounded frontier release required")
        with self.transaction():
            # Page-window debt needs an audited resume, not a release which would
            # silently exceed its immutable binding or strand a blocked task.
            rows = self.db.execute("""SELECT * FROM deferred_frontier WHERE
                COALESCE(json_extract(payload,'$.payload.channel_page'),0) <=
                COALESCE(json_extract(payload,'$.payload.channel.max_pages'),0)
                ORDER BY task_key LIMIT ?""", (limit,)).fetchall()
            for row in rows:
                if not self.db.execute("SELECT 1 FROM tasks WHERE task_key=?", (row["task_key"],)).fetchone():
                    self.enqueue(json.loads(row["payload"]))
                self.db.execute("DELETE FROM deferred_frontier WHERE task_key=?", (row["task_key"],))
        return len(rows)

    def deferred_count(self) -> int:
        return self.db.execute("SELECT count(*) FROM deferred_frontier").fetchone()[0]

    def tasks(self) -> list[dict]:
        return list(self.iter_tasks())

    def requeue(self, task_key: str, *, reason: str):
        if not reason.strip() or len(reason) > 256:
            raise ValueError("explicit requeue reason required")
        with self.transaction():
            result = self.db.execute("UPDATE tasks SET status='pending',reason=?,not_before=0 WHERE task_key=? AND status IN ('blocked','partial')", (reason, task_key))
            if not result.rowcount:
                raise Conflict("only existing blocked/partial tasks can be reviewed for requeue")
            self._event("task_requeued", task_key, timestamp())

    def reserve_request(self, policy_ref: str, url: str, *, max_requests: int, min_interval: float = 1, now: float | None = None):
        now = time.time() if now is None else now
        if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now):
            raise ValueError("finite budget clock required")
        if not policy_ref.strip() or type(max_requests) is not int or max_requests < 1 or not math.isfinite(min_interval) or min_interval < 0:
            raise ValueError("invalid persistent request budget")
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO budgets VALUES (?,0)", (policy_ref,))
            spent = self.db.execute("SELECT spent FROM budgets WHERE policy_ref=?", (policy_ref,)).fetchone()[0]
            if spent >= max_requests:
                raise Conflict("persistent_policy_request_budget_exhausted")
            previous = self.db.execute("SELECT next_at FROM origins WHERE origin=?", (origin,)).fetchone()
            if previous and previous[0] > now:
                raise Conflict("shared_origin_cooldown")
            self.db.execute("UPDATE budgets SET spent=spent+1 WHERE policy_ref=?", (policy_ref,))
            self.db.execute("INSERT INTO origins VALUES (?,?) ON CONFLICT(origin) DO UPDATE SET next_at=excluded.next_at", (origin, now + min_interval))

    def budget_status(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM budgets ORDER BY policy_ref")]

    def register_policy(self, policy_ref: str, checksum: str):
        import re
        if not policy_ref.strip() or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("policy reference and checksum required")
        with self.transaction():
            old = self.db.execute("SELECT checksum,revoked FROM policies WHERE policy_ref=?", (policy_ref,)).fetchone()
            if old and (old[0] != checksum or old[1]):
                raise Conflict("policy revision is immutable or revoked; independent new assessment required")
            self.db.execute("INSERT OR IGNORE INTO policies VALUES (?,?,0)", (policy_ref, checksum))

    def check_policy(self, policy_ref: str):
        row = self.db.execute("SELECT revoked FROM policies WHERE policy_ref=?", (policy_ref,)).fetchone()
        if not row or row[0]:
            raise Conflict("policy missing or revoked")

    def revoke_policy(self, policy_ref: str, *, actor: str, reason: str):
        if not actor.strip() or len(actor) > 256 or not reason.strip() or len(reason) > 2000:
            raise ValueError("revocation requires actor and reason")
        with self.transaction():
            result = self.db.execute("UPDATE policies SET revoked=1 WHERE policy_ref=?", (policy_ref,))
            if not result.rowcount:
                raise Conflict("unknown policy")
            at = timestamp()
            self.db.execute("INSERT INTO policy_audit VALUES (?,?,?,?,?)", (new_id(), policy_ref, actor, reason, at))
            self._event("policy_revoked", policy_ref, at)

    def policy_history(self, policy_ref: str) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM policy_audit WHERE policy_ref=? ORDER BY recorded_at,id", (policy_ref,))]

    def cooldown_origin(self, url: str, until: float):
        if isinstance(until, bool) or not isinstance(until, (int, float)) or not math.isfinite(until):
            raise ValueError("finite cooldown required")
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self.transaction():
            self.db.execute("INSERT INTO origins VALUES (?,?) ON CONFLICT(origin) DO UPDATE SET next_at=max(origins.next_at,excluded.next_at)", (origin, until))

    def origin_ready_at(self, url: str) -> float:
        parts = urlsplit(url)
        row = self.db.execute("SELECT next_at FROM origins WHERE origin=?", (f"{parts.scheme}://{parts.netloc}",)).fetchone()
        return row[0] if row else 0
