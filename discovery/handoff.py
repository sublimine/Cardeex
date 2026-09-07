"""Streaming, snapshot-consistent handoff with the canonical candidate contract."""

import hashlib
import json
from pathlib import Path

from .model import Conflict, canonical_json, timestamp


def handoff_records(store, *, now: str | None = None):
    from jsonschema import Draft202012Validator, FormatChecker
    contract = json.loads((Path(__file__).resolve().parents[1] / "contracts/control.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(contract, format_checker=FormatChecker())
    now = timestamp(now)
    exported = blocked = 0
    checksum = hashlib.sha256()
    with store.transaction():
        yield {"record_type": "discovery_handoff_header", "schema_version": "1.0.0", "generated_at": now,
               "acquire_inventory": False, "inventory_scraped": False, "coverage_ratio": None,
               "evidence_semantics": "unverified claims; independently admitted inventory acquisition required"}
        for row in store.db.execute("SELECT candidate_id FROM candidates ORDER BY candidate_id"):
            cid = row[0]
            try:
                candidate = store.export_candidate(cid, now=now)
                validator.validate(candidate)
                item, evidence = store._live(cid, now)
                record = {"record_type": "discovery_handoff_candidate", "candidate": candidate,
                          "evidence": evidence, "relations": store._live_relations(item["relations"], now)}
                exported += 1
            except Conflict as exc:
                record = {"record_type": "discovery_handoff_blocked", "candidate_id": cid, "reason": str(exc)}
                blocked += 1
            checksum.update((canonical_json(record) + "\n").encode("utf-8"))
            yield record
        for row in store.db.execute("SELECT candidate_id,recorded_at FROM deletion_ledger ORDER BY candidate_id"):
            yield {"record_type": "discovery_deletion_receipt", **dict(row)}
        yield {"record_type": "discovery_handoff_footer", "candidate_records": exported,
               "blocked_records": blocked, "candidate_and_blocked_records_sha256": checksum.hexdigest()}
