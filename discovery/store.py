"""Transactional LOCAL discovery workbench; not the production control database."""

from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

from .model import (CONTRACT_COUNTRIES, DECISIONS, Conflict, Observation, canonical_json,
                    digest, instant, new_id, timestamp)
from .queue import QueueMixin


DDL = """
CREATE TABLE IF NOT EXISTS candidates (
 candidate_id TEXT PRIMARY KEY, candidate_key TEXT UNIQUE NOT NULL, locator TEXT NOT NULL,
 kind TEXT NOT NULL, discovered_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, suppressed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS evidence (
 evidence_id TEXT PRIMARY KEY, evidence_key TEXT UNIQUE NOT NULL,
 candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id), observed_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_candidate ON evidence(candidate_id, observed_at);
CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expires_at);
CREATE TABLE IF NOT EXISTS decisions (
 decision_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
 revision INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(candidate_id,revision));
CREATE TABLE IF NOT EXISTS relations (
 relation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES candidates(candidate_id),
 target_id TEXT NOT NULL REFERENCES candidates(candidate_id), kind TEXT NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
 actor TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(source_id,target_id,kind,evidence_id));
CREATE TABLE IF NOT EXISTS deletion_ledger (
 candidate_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
 actor TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expired_evidence (evidence_key TEXT PRIMARY KEY, expired_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suppressed_artifacts (checksum TEXT PRIMARY KEY, suppressed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans (plan_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
 checksum TEXT PRIMARY KEY, body BLOB NOT NULL, expires_at TEXT NOT NULL, policy_ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
 kind TEXT NOT NULL, entity_id TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channel_pages (
 page_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 body_sha256 TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expired_channel_pages (page_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS channel_resumptions (
 receipt_id TEXT PRIMARY KEY, original_task_key TEXT UNIQUE NOT NULL,
 task_key TEXT NOT NULL, binding_sha256 TEXT NOT NULL, payload TEXT NOT NULL);
"""


class Store(QueueMixin):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        # Avoid the known WAL-reset race on Python's bundled SQLite 3.45.1.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        current = self.db.execute("PRAGMA user_version").fetchone()[0]
        if current not in (0, 1, 2):
            self.db.close()
            raise Conflict("unsupported discovery database migration version")
        self.db.executescript(DDL)
        self._init_queue()
        # v2 prevents v1 workers from treating admitted channel tasks as generic
        # web fetches. Migration is additive and restartable; downgrade is not.
        self.db.execute("PRAGMA user_version=2")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        """Write serialization includes every effect and its audit event."""
        nested = self.db.in_transaction
        savepoint = "sp_" + new_id().replace("-", "")
        if nested:
            self.db.execute(f"SAVEPOINT {savepoint}")
        else:
            self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            if nested:
                self.db.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.db.commit()
        except BaseException:
            if nested:
                self.db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.db.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.db.rollback()
            raise

    def _event(self, kind: str, entity_id: str, now: str):
        self.db.execute("INSERT INTO events(event_id,kind,entity_id,recorded_at) VALUES (?,?,?,?)",
                        (new_id(), kind, entity_id, now))

    def check_replay_policy(self, policy_ref):
        """Local-only assessments need not register a network policy; revoked ones cannot publish."""
        row = self.db.execute("SELECT revoked FROM policies WHERE policy_ref=?", (policy_ref,)).fetchone()
        if row and row[0]:
            raise Conflict("revoked assessment cannot publish replay results")

    def ingest(self, observation: Observation) -> str:
        return self.ingest_batch([observation])[0]

    def ingest_batch(self, observations) -> list[str]:
        ids = []
        with self.transaction():
            for obs in observations:
                if not isinstance(obs, Observation):
                    raise ValueError("Observation required; no implicit coercion of unknown input")
                obs = Observation(**obs.as_dict())
                if self.db.execute("SELECT 1 FROM deletion_ledger WHERE candidate_key=?", (obs.candidate_key,)).fetchone():
                    raise Conflict("suppressed candidate: reimport forbidden")
                if self.db.execute("SELECT 1 FROM expired_evidence WHERE evidence_key=?", (obs.evidence_key,)).fetchone():
                    raise Conflict("expired evidence replay forbidden; obtain a new authorized observation")
                row = self.db.execute("SELECT candidate_id FROM candidates WHERE candidate_key=?", (obs.candidate_key,)).fetchone()
                cid = row[0] if row else new_id()
                if not row:
                    self.db.execute("INSERT INTO candidates(candidate_id,candidate_key,locator,kind,discovered_at,last_observed_at) VALUES (?,?,?,?,?,?)",
                                    (cid, obs.candidate_key, obs.locator, obs.kind, obs.observed_at, obs.observed_at))
                else:
                    # UTC strings may have different fractional precision: compare parsed clocks.
                    old = self.db.execute("SELECT discovered_at,last_observed_at FROM candidates WHERE candidate_id=?", (cid,)).fetchone()
                    first = min(old[0], obs.observed_at, key=instant)
                    last = max(old[1], obs.observed_at, key=instant)
                    self.db.execute("UPDATE candidates SET discovered_at=?,last_observed_at=?,locator=? WHERE candidate_id=?", (first, last, obs.locator, cid))
                inserted = self.db.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?)",
                                          (new_id(), obs.evidence_key, cid, obs.observed_at, obs.expires_at, canonical_json(obs.as_dict()))).rowcount
                if inserted:
                    self._event("evidence_added", cid, obs.observed_at)
                else:
                    existing = self.db.execute("SELECT expires_at FROM evidence WHERE evidence_key=?", (obs.evidence_key,)).fetchone()
                    if instant(obs.expires_at) < instant(existing[0]):
                        self.db.execute("UPDATE evidence SET expires_at=? WHERE evidence_key=?", (obs.expires_at, obs.evidence_key))
                ids.append(cid)
        return ids

    def candidate(self, candidate_id: str) -> dict:
        row = self.db.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError("candidate not found")
        result = dict(row)
        result["evidence"] = [dict(r, claims=json.loads(r["payload"])) for r in self.db.execute(
            "SELECT * FROM evidence WHERE candidate_id=? ORDER BY observed_at,evidence_id", (candidate_id,))]
        for item in result["evidence"]:
            item.pop("payload")
        result["decisions"] = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload FROM decisions WHERE candidate_id=? ORDER BY revision", (candidate_id,))]
        result["decision"] = result["decisions"][-1] if result["decisions"] else {
            "status": "unreviewed", "reason": "Discovery is an unverified claim, not admission",
            "actor_ref": "system:discovery", "decided_at": result["discovered_at"], "target_id": None}
        result["relations"] = [dict(r) for r in self.db.execute(
            "SELECT * FROM relations WHERE source_id=? OR target_id=? ORDER BY relation_id", (candidate_id, candidate_id))]
        return result

    def _live(self, candidate_id: str, now: str) -> tuple[dict, list[dict]]:
        item = self.candidate(candidate_id)
        if item["suppressed"]:
            raise Conflict("candidate is suppressed")
        evidence = [e for e in item["evidence"] if instant(e["observed_at"]) <= instant(now) < instant(e["expires_at"])]
        if not evidence:
            raise Conflict("candidate has no currently retainable observed evidence")
        return item, evidence

    def decide(self, candidate_id: str, status: str, *, actor: str, reason: str,
               expected_revision: int, now: str | None = None, target_id: str | None = None):
        now = timestamp(now)
        if status not in DECISIONS or status == "unreviewed" or not actor.strip() or len(actor) > 256 or not reason.strip() or len(reason) > 2000:
            raise ValueError("explicit bounded decision/actor/reason required")
        if status != "duplicate_candidate" and target_id is not None:
            raise ValueError("target only applies to duplicate candidate decision")
        with self.transaction():
            item, _ = self._live(candidate_id, now)
            if item["revision"] != expected_revision:
                raise Conflict("stale candidate revision")
            if status == "duplicate_candidate":
                if not target_id:
                    raise ValueError("duplicate target required")
                component = self._duplicate_component(candidate_id) | self._duplicate_component(target_id)
                for rel in self.db.execute("SELECT source_id,target_id FROM relations WHERE kind='cannot_link'"):
                    if rel[0] in component and rel[1] in component:
                        raise Conflict("duplicate components violate cannot_link")
                seen = {candidate_id}
                current = target_id
                while current:
                    if current in seen:
                        raise Conflict("duplicate decision cycle")
                    seen.add(current)
                    if self.db.execute("SELECT 1 FROM relations WHERE kind='cannot_link' AND ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))",
                                       (candidate_id, current, current, candidate_id)).fetchone():
                        raise Conflict("explicit cannot_link prevents duplicate candidate decision")
                    target, _ = self._live(current, now)
                    current = target["decision"]["target_id"] if target["decision"]["status"] == "duplicate_candidate" else None
            decision = dict(status=status, actor_ref=actor, reason=reason, decided_at=now, target_id=target_id)
            revision = expected_revision + 1
            self.db.execute("INSERT INTO decisions VALUES (?,?,?,?)", (new_id(), candidate_id, revision, canonical_json(decision)))
            self.db.execute("UPDATE candidates SET revision=? WHERE candidate_id=?", (revision, candidate_id))
            self._event("candidate_decided", candidate_id, now)
        return decision

    def _duplicate_component(self, candidate_id: str) -> set[str]:
        rows = self.db.execute("""WITH RECURSIVE edges(a,b) AS (
          SELECT d.candidate_id,json_extract(d.payload,'$.target_id') FROM decisions d
          WHERE json_extract(d.payload,'$.status')='duplicate_candidate'
            AND d.revision=(SELECT MAX(r.revision) FROM decisions r WHERE r.candidate_id=d.candidate_id)),
          component(node) AS (SELECT ? UNION
            SELECT CASE WHEN e.a=c.node THEN e.b ELSE e.a END
            FROM edges e JOIN component c ON e.a=c.node OR e.b=c.node)
          SELECT node FROM component LIMIT 10001""", (candidate_id,)).fetchall()
        if len(rows) > 10000:
            raise Conflict("duplicate component exceeds local review bound")
        return {r[0] for r in rows}

    def relate(self, source_id: str, target_id: str, kind: str, *, evidence_id: str, actor: str, now: str | None = None):
        now = timestamp(now)
        if kind not in ("possible_same_identity", "cannot_link", "operates", "located_at", "publishes", "inventory_surface", "outbound_link"):
            raise ValueError("unknown relation type")
        if source_id == target_id or not actor.strip():
            raise ValueError("distinct candidates and actor required")
        with self.transaction():
            self._live(source_id, now)
            self._live(target_id, now)
            ev = self.db.execute("SELECT * FROM evidence WHERE evidence_id=? AND candidate_id IN (?,?)", (evidence_id, source_id, target_id)).fetchone()
            if not ev or not instant(ev["observed_at"]) <= instant(now) < instant(ev["expires_at"]):
                raise Conflict("relation evidence not available for these candidates")
            self.db.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?,?,?,?)",
                            (new_id(), source_id, target_id, kind, evidence_id, actor, now))
            self._event("relation_recorded", source_id, now)

    def _exportable(self, candidate_id: str, now: str):
        item, evidence = self._live(candidate_id, now)
        if any(set(e["claims"]["countries"]) - set(CONTRACT_COUNTRIES) for e in evidence):
            raise Conflict("country requires canonical control contract migration before handoff")
        decision = item["decision"]
        if decision["status"] == "duplicate_candidate":
            component = self._duplicate_component(candidate_id)
            if any(r[0] in component and r[1] in component for r in self.db.execute("SELECT source_id,target_id FROM relations WHERE kind='cannot_link'")):
                raise Conflict("new identity contradiction requires duplicate decision review")
        seen = {candidate_id}
        while decision["status"] == "duplicate_candidate":
            target = decision["target_id"]
            if target in seen:
                raise Conflict("duplicate chain invalid")
            seen.add(target)
            target_item, target_evidence = self._live(target, now)
            if any(set(e["claims"]["countries"]) - set(CONTRACT_COUNTRIES) for e in target_evidence):
                raise Conflict("duplicate target requires contract migration")
            decision = target_item["decision"]
        return item, evidence

    def export_candidate(self, candidate_id: str, *, now: str | None = None) -> dict:
        now = timestamp(now)
        item, evidence = self._exportable(candidate_id, now)
        countries = sorted({x for e in evidence for x in e["claims"]["countries"]})
        if set(countries) - set(CONTRACT_COUNTRIES):
            raise Conflict("country requires canonical control contract migration before handoff")
        claims = min(evidence, key=lambda e: instant(e["observed_at"]))["claims"]
        live_relations = self._live_relations(item["relations"], now)
        related = sorted({r["target_id"] if r["source_id"] == candidate_id else r["source_id"] for r in live_relations})
        due = min(instant(e["expires_at"]) for e in evidence)
        due = min(due, instant(item["last_observed_at"]) + timedelta(days=30))
        return dict(record_type="discovery_candidate", schema_version="1.0.0", candidate_id=candidate_id,
                    claimed_kind=item["kind"], observed_locator=item["locator"], discovered_at=item["discovered_at"],
                    evidence_refs=[e["evidence_id"] for e in evidence], method=claims["method"], method_version=claims["method_version"],
                    claimed_countries=countries, suspected_classes=sorted({x for e in evidence for x in e["claims"]["classes"]}),
                    related_candidate_ids=related, decision=item["decision"], review_due_at=timestamp(due.isoformat()))

    def handoff(self, *, now: str | None = None) -> dict:
        """One consistent snapshot; companion ledger preserves claim/relationship provenance."""
        now = timestamp(now)
        candidates, evidence, relations, blocked = [], {}, {}, []
        with self.transaction():
            for row in self.db.execute("SELECT candidate_id FROM candidates ORDER BY candidate_id"):
                cid = row[0]
                try:
                    exported = self.export_candidate(cid, now=now)
                    item, retained = self._live(cid, now)
                except Conflict as exc:
                    blocked.append({"candidate_id": cid, "reason": str(exc)})
                    continue
                candidates.append(exported)
                evidence.update({e["evidence_id"]: e for e in retained})
                relations.update({r["relation_id"]: r for r in self._live_relations(item["relations"], now)})
            deletions = [dict(r) for r in self.db.execute("SELECT candidate_id,recorded_at FROM deletion_ledger ORDER BY candidate_id")]
        return dict(schema_version="1.0.0", generated_at=now, candidates=candidates,
                    evidence=list(evidence.values()), relations=list(relations.values()),
                    blocked=blocked, deletion_receipts=deletions, acquire_inventory=False)

    def _live_relations(self, relations: list[dict], now: str) -> list[dict]:
        retained = []
        for relation in relations:
            ev = self.db.execute("SELECT observed_at,expires_at FROM evidence WHERE evidence_id=?", (relation["evidence_id"],)).fetchone()
            if not ev or not instant(ev[0]) <= instant(now) < instant(ev[1]):
                continue
            try:
                self._exportable(relation["source_id"], now)
                self._exportable(relation["target_id"], now)
            except Conflict:
                continue
            retained.append(relation)
        return retained

    def expire(self, *, now: str | None = None) -> dict:
        now = timestamp(now)
        with self.transaction():
            expired = [dict(r) for r in self.db.execute("SELECT evidence_id,evidence_key,candidate_id FROM evidence WHERE julianday(expires_at)<=julianday(?)", (now,))]
            for row in expired:
                self.db.execute("INSERT OR IGNORE INTO expired_evidence VALUES (?,?)", (row["evidence_key"], now))
                self.db.execute("DELETE FROM evidence WHERE evidence_id=?", (row["evidence_id"],))
                self._event("evidence_expired", row["candidate_id"], now)
            artifacts = self.db.execute("DELETE FROM artifacts WHERE julianday(expires_at)<=julianday(?)", (now,)).rowcount
            self.db.execute("INSERT OR IGNORE INTO expired_channel_pages SELECT page_id FROM channel_pages WHERE julianday(expires_at)<=julianday(?)", (now,))
            self.db.execute("DELETE FROM channel_pages WHERE julianday(expires_at)<=julianday(?)", (now,))
            self.db.execute("UPDATE candidates SET locator='' WHERE NOT EXISTS(SELECT 1 FROM evidence WHERE evidence.candidate_id=candidates.candidate_id)")
        return {"evidence_removed": len(expired), "artifacts_removed": artifacts}

    def suppress(self, candidate_id: str, *, actor: str, reason: str, now: str | None = None):
        now = timestamp(now)
        if not actor.strip() or not reason.strip():
            raise ValueError("suppression requires actor and reason")
        with self.transaction():
            item = self.candidate(candidate_id)
            self.db.execute("INSERT OR IGNORE INTO deletion_ledger VALUES (?,?,?,?,?)", (item["candidate_key"], candidate_id, now, actor, reason))
            # Content hashes may be shared. Removing the artifact is conservative and visible.
            for ev in item["evidence"]:
                if ev["claims"]["body_sha256"]:
                    self.db.execute("INSERT OR IGNORE INTO suppressed_artifacts VALUES (?,?)", (ev["claims"]["body_sha256"], now))
                    self.db.execute("DELETE FROM artifacts WHERE checksum=?", (ev["claims"]["body_sha256"],))
                    self.db.execute("DELETE FROM channel_pages WHERE body_sha256=?", (ev["claims"]["body_sha256"],))
            self.db.execute("DELETE FROM evidence WHERE candidate_id=?", (candidate_id,))
            self.db.execute("DELETE FROM relations WHERE source_id=? OR target_id=?", (candidate_id, candidate_id))
            self.db.execute("DELETE FROM decisions WHERE candidate_id=?", (candidate_id,))
            self.db.execute("UPDATE candidates SET locator='',suppressed=1,revision=revision+1 WHERE candidate_id=?", (candidate_id,))
            self._event("candidate_suppressed", candidate_id, now)

    def put_artifact(self, body: bytes, *, expires_at: str, policy_ref: str) -> str:
        import hashlib
        if len(body) > 4 * 1024 * 1024 or not policy_ref.strip():
            raise ValueError("artifact size or policy invalid")
        checksum = hashlib.sha256(body).hexdigest()
        expires_at = timestamp(expires_at)
        with self.transaction():
            if self.db.execute("SELECT 1 FROM suppressed_artifacts WHERE checksum=?", (checksum,)).fetchone():
                raise Conflict("suppressed artifact cannot be restored or reimported")
            existing = self.db.execute("SELECT expires_at FROM artifacts WHERE checksum=?", (checksum,)).fetchone()
            if existing:
                expires_at = min(expires_at, existing[0], key=instant)
                self.db.execute("UPDATE artifacts SET expires_at=? WHERE checksum=?", (expires_at, checksum))
            else:
                self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (checksum, body, expires_at, policy_ref))
        return checksum

    def artifact(self, checksum: str, *, now: str | None = None) -> bytes:
        row = self.db.execute("SELECT body,expires_at FROM artifacts WHERE checksum=?", (checksum,)).fetchone()
        if not row or instant(row[1]) <= instant(timestamp(now)):
            raise Conflict("retained artifact unavailable")
        return bytes(row[0])

    def count_candidates(self) -> int:
        return self.db.execute("SELECT count(*) FROM candidates WHERE suppressed=0").fetchone()[0]

    def iter_observations(self, now: str):
        now = timestamp(now)
        for row in self.db.execute("SELECT e.*,c.kind FROM evidence e JOIN candidates c USING(candidate_id) WHERE c.suppressed=0 ORDER BY e.evidence_id"):
            if instant(row["observed_at"]) <= instant(now) < instant(row["expires_at"]):
                data = json.loads(row["payload"])
                decision = self.db.execute("SELECT payload FROM decisions WHERE candidate_id=? ORDER BY revision DESC LIMIT 1", (row["candidate_id"],)).fetchone()
                yield dict(data, candidate_id=row["candidate_id"], evidence_id=row["evidence_id"],
                           decision_status=json.loads(decision[0])["status"] if decision else "unreviewed")

    def save_plan(self, plan: dict):
        stored = {k: v for k, v in plan.items() if k != "tasks"}
        # Each emitted page remains separate; a partial generation cannot erase prior debt.
        key = f"{plan['plan_id']}:{plan.get('offset', 0)}"
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO plans VALUES (?,?)", (key, canonical_json(stored)))
            for task in plan["tasks"]:
                self.enqueue(task)

    def list_plans(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM plans ORDER BY plan_id")]

    def record_channel_page(self, task, origin, result, observed_at, checksum, *, expires_at, raw_retained=True):
        """Persist empty/truncated outcomes too, with retention and immutable replay identity."""
        from .channels import CHANNEL_VERSION, validate_binding
        from .transport import normalize_url
        binding = validate_binding(task["payload"]["channel"])
        observed_at, expires_at = timestamp(observed_at), timestamp(expires_at)
        if instant(expires_at) <= instant(observed_at) or instant(observed_at) > instant(timestamp()):
            raise ValueError("invalid channel observation/retention window")
        page_id = digest([task["task_key"], observed_at, checksum, digest(binding)])
        payload = dict(task_key=task["task_key"], plan_id=task["plan_id"],
            origin=normalize_url(origin), adapter=binding["adapter"], method_version=CHANNEL_VERSION,
            channel_sha256=digest(binding), policy_ref=binding["assessment_ref"],
            row_count=len(result["rows"]) if result["rows"] is not None else None,
            complete=result["complete"], warnings=result["warnings"],
            continuation_present=result["continuation"] is not None, raw_retained=raw_retained)
        with self.transaction():
            if self.db.execute("SELECT 1 FROM suppressed_artifacts WHERE checksum=?", (checksum,)).fetchone() or self.db.execute(
                    "SELECT 1 FROM expired_channel_pages WHERE page_id=?", (page_id,)).fetchone():
                raise Conflict("suppressed or expired channel outcome cannot be replayed")
            old = self.db.execute("SELECT expires_at FROM channel_pages WHERE page_id=?", (page_id,)).fetchone()
            if old:
                expires_at = min(old[0], expires_at, key=instant)
            self.db.execute("INSERT INTO channel_pages VALUES (?,?,?,?,?) ON CONFLICT(page_id) DO UPDATE SET expires_at=excluded.expires_at",
                            (page_id, observed_at, expires_at, checksum, canonical_json(payload)))
        return page_id

    def channel_summary(self, now):
        """Source-query outcomes are not market/inventory coverage certificates."""
        from collections import Counter
        counts, adapters, warnings = Counter(), Counter(), Counter()
        for row in self.db.execute("SELECT payload FROM channel_pages WHERE julianday(observed_at)<=julianday(?) AND julianday(expires_at)>julianday(?)", (now, now)):
            item = json.loads(row[0])
            counts["pages"] += 1
            counts["rows"] += item["row_count"] or 0
            counts["rows_unknown_pages"] += item["row_count"] is None
            counts["unknown_pages" if item["complete"] is None else "complete_query_pages" if item["complete"] else "incomplete_pages"] += 1
            counts["pages_with_continuation"] += item["continuation_present"]
            adapters[item["adapter"]] += 1
            warnings.update(item["warnings"])
        return {key: counts[key] for key in ("pages", "rows", "rows_unknown_pages", "unknown_pages", "complete_query_pages", "incomplete_pages", "pages_with_continuation")} | {
            "adapters": dict(adapters), "warnings": dict(warnings), "coverage_ratio": None,
            "unit": "retained_source_query_page_not_unique_entity"}
