"""Durability, uncertainty and fencing acceptance tests on real SQLite."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.store"), "D2 store is absent")
        from discovery.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "discovery.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def observation(self, **overrides):
        from discovery.model import Observation
        data = dict(locator="https://garage.example/stock", kind="source", method="directory",
                    method_version="1", origin_locator="https://directory.example/item/001",
                    evidence_group="directory:first", observed_at="2026-09-07T10:00:00Z",
                    expires_at="2026-10-07T10:00:00Z", policy_ref="test:synthetic",
                    countries=("FR",), classes=("car",), source_type="dealer_owned",
                    signals={"label": "Garage trois véhicules", "stock_claim": 3})
        data.update(overrides)
        return Observation(**data)

    def test_exact_replay_is_idempotent_but_independent_evidence_survives(self):
        a = self.store.ingest(self.observation())
        self.assertEqual(a, self.store.ingest(self.observation()))
        self.store.ingest(self.observation(evidence_group="brand:independent", method="brand"))
        detail = self.store.candidate(a)
        self.assertEqual(len(detail["evidence"]), 2)
        self.assertEqual(detail["decision"]["status"], "unreviewed")

    def test_stock_three_or_unknown_never_filters_candidate(self):
        for stock in (0, 1, 3, None):
            key = self.store.ingest(self.observation(locator=f"https://garage.example/{stock}", signals={"stock_claim": stock}))
            self.assertEqual(self.store.candidate(key)["revision"], 1)

    def test_shared_domain_does_not_merge_distinct_locations_or_kinds(self):
        ids = {self.store.ingest(self.observation(locator=f"https://group.example/{pos}", kind=kind))
               for pos in ("one", "two") for kind in ("source", "point_of_sale")}
        self.assertEqual(len(ids), 4)

    def test_candidate_is_not_country_fact_or_inventory_permission(self):
        key = self.store.ingest(self.observation(countries=(), classes=(), source_type="unknown"))
        data = self.store.export_candidate(key, now="2026-09-08T00:00:00Z")
        self.assertEqual(data["claimed_countries"], [])
        self.assertEqual(data["suspected_classes"], [])
        self.assertNotIn("acquire_inventory", data)

    def test_late_observation_does_not_replace_newer_knowledge(self):
        key = self.store.ingest(self.observation(observed_at="2026-09-08T10:00:00Z"))
        self.store.ingest(self.observation(observed_at="2026-09-01T10:00:00Z"))
        self.assertEqual(self.store.candidate(key)["last_observed_at"], "2026-09-08T10:00:00Z")

    def test_decision_requires_live_evidence_and_expected_revision(self):
        from discovery.model import Conflict
        key = self.store.ingest(self.observation())
        self.store.decide(key, "accepted", actor="operator:test", reason="synthetic review",
                          expected_revision=1, now="2026-09-08T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.decide(key, "rejected", actor="operator:test", reason="stale write",
                              expected_revision=1, now="2026-09-08T00:00:00Z")
        self.assertEqual(len(self.store.candidate(key)["decisions"]), 1)

    def test_expired_evidence_does_not_export_as_verified(self):
        from discovery.model import Conflict
        key = self.store.ingest(self.observation())
        with self.assertRaises(Conflict):
            self.store.export_candidate(key, now="2026-11-01T00:00:00Z")

    def test_relations_retain_kind_evidence_without_entity_merge(self):
        a = self.store.ingest(self.observation())
        b = self.store.ingest(self.observation(locator="https://garage.example/branch", kind="point_of_sale"))
        ev = self.store.candidate(a)["evidence"][0]["evidence_id"]
        self.store.relate(a, b, "possible_same_identity", evidence_id=ev, actor="reviewer", now="2026-09-08T00:00:00Z")
        self.assertEqual(len(self.store.candidate(a)["relations"]), 1)
        self.assertEqual(self.store.candidate(b)["decision"]["status"], "unreviewed")

    def test_duplicate_decision_rejects_cycles(self):
        from discovery.model import Conflict
        a = self.store.ingest(self.observation())
        b = self.store.ingest(self.observation(locator="https://other.example"))
        self.store.decide(a, "duplicate_candidate", actor="r", reason="same locator evidence",
                          expected_revision=1, target_id=b, now="2026-09-08T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.decide(b, "duplicate_candidate", actor="r", reason="cycle",
                              expected_revision=1, target_id=a, now="2026-09-08T00:00:00Z")

    def test_suppression_blocks_reimport_and_handoff(self):
        from discovery.model import Conflict
        obs = self.observation()
        key = self.store.ingest(obs)
        self.store.suppress(key, actor="policy", reason="test erasure", now="2026-09-08T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.ingest(obs)
        with self.assertRaises(Conflict):
            self.store.export_candidate(key, now="2026-09-08T00:00:00Z")
        self.assertEqual(self.store.candidate(key)["evidence"], [])

    def test_seventh_country_is_preserved_but_handoff_needs_contract_change(self):
        from discovery.model import Conflict
        key = self.store.ingest(self.observation(countries=("IT",)))
        with self.assertRaises(Conflict):
            self.store.export_candidate(key, now="2026-09-08T00:00:00Z")

    def test_transaction_rolls_back_partial_bad_batch(self):
        with self.assertRaises(ValueError):
            self.store.ingest_batch([self.observation(), {"bad": "row"}])
        self.assertEqual(self.store.count_candidates(), 0)

    def test_reopen_retains_state(self):
        from discovery.store import Store
        key = self.store.ingest(self.observation())
        with Store(self.path) as other:
            self.assertEqual(other.candidate(key)["locator"], self.observation().locator)

    def task(self, key, stratum="FR:rural", priority=50):
        return dict(task_key=key, plan_id="p1", stratum=stratum, country="FR", classes=["car"],
                    strategy="test", kind="fetch", locator=f"https://garage.example/{key}",
                    priority=priority, access_mode="public_web", payload={})

    def test_task_replay_is_idempotent(self):
        self.store.enqueue(self.task("a"))
        self.store.enqueue(self.task("a"))
        self.assertEqual(len(self.store.tasks()), 1)

    def test_fairness_gives_low_yield_stratum_a_turn(self):
        for n in range(8):
            self.store.enqueue(self.task(f"big{n}", "FR:metro", 100))
        self.store.enqueue(self.task("small", "FR:rural", 1))
        first = self.store.claim("worker", now=100, lease_seconds=30)
        self.store.finish(first["task_key"], first["lease_token"], "done", now=101)
        second = self.store.claim("worker", now=102, lease_seconds=30)
        self.assertNotEqual(first["stratum"], second["stratum"])

    def test_expired_worker_cannot_commit_after_reclaim(self):
        from discovery.model import Conflict
        self.store.enqueue(self.task("a"))
        a = self.store.claim("one", now=100, lease_seconds=10)
        b = self.store.claim("two", now=111, lease_seconds=10)
        self.assertNotEqual(a["lease_token"], b["lease_token"])
        with self.assertRaises(Conflict):
            self.store.finish("a", a["lease_token"], "done", now=112)
        self.store.finish("a", b["lease_token"], "done", now=112)

    def test_retry_and_blocked_are_not_success_or_zero_inventory(self):
        self.store.enqueue(self.task("a"))
        job = self.store.claim("one", now=100, lease_seconds=10)
        self.store.finish("a", job["lease_token"], "retry", reason="rate_limited", now=101, retry_at=160)
        self.assertIsNone(self.store.claim("two", now=159))
        job = self.store.claim("two", now=161)
        self.store.finish("a", job["lease_token"], "blocked", reason="permission_missing", now=162)
        self.assertEqual(self.store.tasks()[0]["status"], "blocked")

    def test_expired_worker_cannot_insert_any_evidence_or_children(self):
        from discovery.model import Conflict
        self.store.enqueue(self.task("a"))
        old = self.store.claim("old", now=100, lease_seconds=10)
        self.store.claim("new", now=111, lease_seconds=10)
        with self.assertRaises(Conflict):
            self.store.commit_result("a", old["lease_token"], [self.observation()],
                                     now=112, next_tasks=[self.task("child")])
        self.assertEqual(self.store.count_candidates(), 0)
        self.assertEqual(len(self.store.tasks()), 1)

    def test_budget_is_shared_across_reopen_and_counts_attempts(self):
        from discovery.model import Conflict
        from discovery.store import Store
        self.store.reserve_request("p", "https://garage.example/", max_requests=1, now=100)
        with Store(self.path) as second:
            with self.assertRaises(Conflict):
                second.reserve_request("p", "https://another.example/", max_requests=1, now=102)
        self.assertEqual(self.store.budget_status(), [{"policy_ref": "p", "spent": 1}])

    def test_http_only_dealer_handoff_passes_existing_control_validator(self):
        import json
        from jsonschema import Draft202012Validator, FormatChecker
        key = self.store.ingest(self.observation(locator="http://garage.example/stock"))
        contract = json.loads((Path(__file__).parents[2] / "contracts/control.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(contract, format_checker=FormatChecker()).validate(
            self.store.export_candidate(key, now="2026-09-08T00:00:00Z"))

    def test_handoff_filters_expired_relation_evidence(self):
        a = self.store.ingest(self.observation(expires_at="2026-09-09T10:00:00Z"))
        b = self.store.ingest(self.observation(locator="https://other.example/"))
        ev = self.store.candidate(a)["evidence"][0]["evidence_id"]
        self.store.relate(a, b, "operates", evidence_id=ev, actor="r", now="2026-09-08T00:00:00Z")
        data = self.store.handoff(now="2026-09-10T00:00:00Z")
        self.assertEqual(data["relations"], [])
        self.assertEqual(data["candidates"][0]["related_candidate_ids"], [])

    def test_expiry_cleanup_retains_no_claim_payload(self):
        key = self.store.ingest(self.observation(expires_at="2026-09-09T10:00:00Z"))
        result = self.store.expire(now="2026-09-10T00:00:00Z")
        self.assertEqual(result["evidence_removed"], 1)
        self.assertEqual(self.store.candidate(key)["evidence"], [])

    def test_cannot_link_blocks_duplicate_decision(self):
        from discovery.model import Conflict
        a = self.store.ingest(self.observation())
        b = self.store.ingest(self.observation(locator="https://other.example/"))
        ev = self.store.candidate(a)["evidence"][0]["evidence_id"]
        self.store.relate(a, b, "cannot_link", evidence_id=ev, actor="r", now="2026-09-08T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.decide(a, "duplicate_candidate", actor="r", reason="contradiction", expected_revision=1,
                              target_id=b, now="2026-09-08T00:00:00Z")

    def test_nested_failed_batch_cannot_commit_prefix(self):
        with self.store.transaction():
            try:
                self.store.ingest_batch([self.observation(), {}])
            except ValueError:
                pass
        self.assertEqual(self.store.count_candidates(), 0)

    def test_mutated_observation_is_revalidated_at_admission(self):
        obs = self.observation()
        obs.signals["oversized"] = "x" * 20000
        with self.assertRaises(ValueError):
            self.store.ingest(obs)

    def test_duplicate_with_expired_target_cannot_escape_handoff(self):
        a = self.store.ingest(self.observation())
        b = self.store.ingest(self.observation(locator="https://other.example/", expires_at="2026-09-09T00:00:00Z"))
        self.store.decide(a, "duplicate_candidate", actor="r", reason="test", expected_revision=1,
                          target_id=b, now="2026-09-08T00:00:00Z")
        report = self.store.handoff(now="2026-09-10T00:00:00Z")
        self.assertEqual(report["candidates"], [])
        self.assertEqual(len(report["blocked"]), 2)

    def test_transitive_cannot_link_considers_inbound_aliases(self):
        from discovery.model import Conflict
        a, b, c = [self.store.ingest(self.observation(locator=f"https://garage.example/{x}")) for x in "abc"]
        ev = self.store.candidate(a)["evidence"][0]["evidence_id"]
        self.store.relate(a, c, "cannot_link", evidence_id=ev, actor="r", now="2026-09-08T00:00:00Z")
        self.store.decide(a, "duplicate_candidate", actor="r", reason="test", expected_revision=1, target_id=b, now="2026-09-08T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.decide(b, "duplicate_candidate", actor="r", reason="test", expected_revision=1, target_id=c, now="2026-09-08T00:00:00Z")

    def test_suppressed_artifact_cannot_be_reinserted(self):
        import hashlib
        from discovery.model import Conflict
        body = b"synthetic personal material"
        checksum = self.store.put_artifact(body, expires_at="2026-10-07T00:00:00Z", policy_ref="test")
        key = self.store.ingest(self.observation(body_sha256=checksum))
        self.store.suppress(key, actor="p", reason="erase", now="2026-09-08T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.put_artifact(body, expires_at="2026-11-07T00:00:00Z", policy_ref="test")

    def test_extending_retention_does_not_resurrect_expired_observation(self):
        from discovery.model import Conflict
        self.store.ingest(self.observation(expires_at="2026-09-09T00:00:00Z"))
        self.store.expire(now="2026-09-10T00:00:00Z")
        with self.assertRaises(Conflict):
            self.store.ingest(self.observation(expires_at="2026-10-09T00:00:00Z"))

    def test_nan_does_not_bypass_worker_fence(self):
        self.store.enqueue(self.task("a"))
        task = self.store.claim("r", now=100, lease_seconds=10)
        with self.assertRaises(ValueError):
            self.store.finish("a", task["lease_token"], "done", now=float("nan"))

    def test_spa_dealers_remain_different_candidates(self):
        a = self.store.ingest(self.observation(locator="https://garage.example/#/dealer/a"))
        b = self.store.ingest(self.observation(locator="https://garage.example/#/dealer/b"))
        self.assertNotEqual(a, b)

    def test_new_cannot_link_invalidates_existing_duplicate_handoff(self):
        a = self.store.ingest(self.observation())
        b = self.store.ingest(self.observation(locator="https://other.example/"))
        self.store.decide(a, "duplicate_candidate", actor="r", reason="test", expected_revision=1, target_id=b, now="2026-09-08T00:00:00Z")
        ev = self.store.candidate(a)["evidence"][0]["evidence_id"]
        self.store.relate(a, b, "cannot_link", evidence_id=ev, actor="r", now="2026-09-08T00:00:00Z")
        report = self.store.handoff(now="2026-09-08T00:00:00Z")
        self.assertFalse(any(row["candidate_id"] == a for row in report["candidates"]))

    def test_handoff_closes_relations_over_exportable_endpoints(self):
        a = self.store.ingest(self.observation())
        b = self.store.ingest(self.observation(locator="https://other.example/", countries=("IT",)))
        ev = self.store.candidate(b)["evidence"][0]["evidence_id"]
        self.store.relate(a, b, "operates", evidence_id=ev, actor="r", now="2026-09-08T00:00:00Z")
        report = self.store.handoff(now="2026-09-08T00:00:00Z")
        self.assertEqual(report["relations"], [])
        self.assertEqual(report["candidates"][0]["related_candidate_ids"], [])

    def test_registered_policy_revision_is_immutable_and_revocable(self):
        from discovery.model import Conflict
        self.assertTrue(callable(getattr(self.store, "register_policy", None)))
        self.store.register_policy("policy:1", "a" * 64)
        self.store.register_policy("policy:1", "a" * 64)
        with self.assertRaises(Conflict):
            self.store.register_policy("policy:1", "b" * 64)
        self.store.revoke_policy("policy:1", actor="test", reason="withdrawn")
        with self.assertRaises(Conflict):
            self.store.check_policy("policy:1")
        self.assertTrue(callable(getattr(self.store, "policy_history", None)))
        receipt = self.store.policy_history("policy:1")[-1]
        self.assertEqual((receipt["actor"], receipt["reason"]), ("test", "withdrawn"))

    def test_origin_retry_after_survives_reopen(self):
        from discovery.store import Store
        from discovery.model import Conflict
        self.assertTrue(callable(getattr(self.store, "cooldown_origin", None)))
        self.store.cooldown_origin("https://garage.example/a", 200)
        with Store(self.path) as second:
            with self.assertRaises(Conflict):
                second.reserve_request("p", "https://garage.example/b", max_requests=10, now=150)
            second.reserve_request("p", "https://garage.example/b", max_requests=10, now=201)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.model"), "D2 model is absent")

    def test_uuid7_is_opaque_and_correct_variant(self):
        import uuid
        from discovery.model import new_id
        ids = {new_id() for _ in range(500)}
        self.assertEqual(len(ids), 500)
        self.assertTrue(all(uuid.UUID(x).version == 7 for x in ids))

    def test_naive_or_invalid_clock_rejected(self):
        from discovery.model import timestamp
        for value in ("2026-09-07", "2026-02-30T00:00:00Z", "2026-09-07T00:00:00"):
            with self.assertRaises(ValueError):
                timestamp(value)


if __name__ == "__main__":
    unittest.main()
