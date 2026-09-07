"""Replay, retained source outcomes and explicit continuation recovery."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from discovery.channels import validate_binding, request_url
from discovery.engine import inspect_channel, resume_channel, run_worker
from discovery.model import digest, timestamp
from discovery.store import Store
from discovery.transport import AccessPolicy, FetchResult


class ChannelOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "work.sqlite"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.at = timestamp()
        self.expiry = timestamp((datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        self.binding = validate_binding(dict(adapter="searxng_json", endpoint="https://search.example/search",
            assessment_ref="test:admission", countries=["ES"], vehicle_classes=["car"], parameters={}, max_pages=1))

    def test_sirene_import_withholds_original_private_columns_and_retains_outcome(self):
        binding = dict(self.binding, adapter="sirene_csv", countries=["FR"], endpoint="https://registry.example/sirene",
            parameters=dict(snapshot_date="2026-09-01", activity_codes=["45.11Z"], nomenclature="NAFRev2"))
        body = ("siret,statutDiffusionEtablissement,etatAdministratifEtablissement,activitePrincipaleEtablissement,nomenclatureActivitePrincipaleEtablissement,nomUniteLegale\n"
                "12345678901234,O,A,45.11Z,NAFRev2,PRIVATE_MARKER\n"
                "12345678901235,P,A,45.11Z,NAFRev2,RESTRICTED_MARKER\n").encode()
        result = inspect_channel(self.store, body, "text/csv", binding["endpoint"], binding=binding,
            policy_ref="test:admission", observed_at=self.at, expires_at=self.expiry)
        self.assertEqual(len(result["candidate_ids"]), 1)
        self.assertFalse(result["raw_retained"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
        exported = json.dumps(self.store.handoff())
        self.assertNotIn("PRIVATE_MARKER", exported)
        self.assertNotIn("RESTRICTED_MARKER", exported)
        self.assertIn("raw_withheld", exported)
        self.assertEqual(self.store.channel_summary(self.at)["pages"], 1)
        self.store.suppress(result["candidate_ids"][0], actor="test", reason="retention test")
        self.assertEqual(self.store.channel_summary(self.at)["pages"], 0)
        with self.assertRaises(ValueError):
            inspect_channel(self.store, body, "text/csv", binding["endpoint"], binding=binding,
                policy_ref="other", observed_at=self.at, expires_at=self.expiry)

    def test_resume_page_debt_is_reviewed_immutable_and_does_not_reset_budget(self):
        task = dict(task_key="q", plan_id="p", stratum="ES:test", country="ES", classes=["car"],
            strategy="synthetic", kind="query", locator="garage", priority=10, access_mode="manual_review",
            payload=dict(channel=self.binding, channel_checksum=digest(self.binding), channel_page=1))
        self.store.enqueue(task)
        policy = AccessPolicy(allowed_hosts=("search.example",), operator="test", policy_ref="test:admission",
            expires_at=self.expiry, min_interval_seconds=0)
        class Fake:
            def fetch(_, url):
                return FetchResult(url, 200, {"content-type": "application/json"}, b'{"results":[{"url":"https://dealer.example/car/1"}]}')
        run_worker(self.store, policy, max_tasks=1, fetcher=Fake())
        debt = self.store.db.execute("SELECT task_key FROM deferred_frontier").fetchone()[0]
        self.assertEqual(self.store.release_deferred(), 0)
        self.store.reserve_request("test:admission", self.binding["endpoint"], max_requests=20, min_interval=0)
        before = self.store.budget_status()
        result = resume_channel(self.store, debt, self.binding, actor="test", reason="next reviewed window")
        self.assertEqual(self.store.deferred_count(), 0)
        self.assertEqual(before, self.store.budget_status())
        again = resume_channel(self.store, debt, self.binding, actor="test", reason="repeat command")
        self.assertEqual(result["task_key"], again["task_key"])
        resumed = next(t for t in self.store.tasks() if t["task_key"] == result["task_key"])
        self.assertEqual(resumed["payload"]["channel_cursor"], "2")
        self.assertEqual(resumed["payload"]["channel_page"], 1)
        self.assertNotEqual(resumed["plan_id"], "p")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(before, self.store.budget_status())

    def test_empty_page_ledger_expires_and_cannot_extend_by_replay(self):
        origin = request_url(self.binding, {"query": "garage"})
        result = inspect_channel(self.store, b'{"results":[]}', "application/json", origin,
            binding=self.binding, policy_ref="test:admission", observed_at=self.at, expires_at=self.expiry)
        self.assertEqual(result["candidate_ids"], [])
        self.assertIsNone(result["complete"])
        self.assertEqual(self.store.channel_summary(self.at)["unknown_pages"], 1)
        later = timestamp((datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
        self.store.expire(now=later)
        self.assertEqual(self.store.channel_summary(later)["pages"], 0)

    def test_governed_transport_charges_requests_and_stops_inventory_redirect(self):
        task = dict(task_key="physical", plan_id="p", stratum="ES:test", country="ES", classes=["car"],
            strategy="synthetic", kind="query", locator="garage", priority=10, access_mode="manual_review",
            payload=dict(channel=self.binding, channel_checksum=digest(self.binding), channel_page=1))
        self.store.enqueue(task)
        policy = AccessPolicy(allowed_hosts=("search.example", "dealer.example"), operator="test", policy_ref="test:admission",
            expires_at=self.expiry, min_interval_seconds=0, max_requests=10)
        calls = []
        def physical(url, *_):
            calls.append(url)
            if url.endswith("/robots.txt"):
                return FetchResult(url, 404, {}, b"")
            if url.startswith("https://search.example/"):
                return FetchResult(url, 200, {"content-type": "application/json"}, b'{"results":[{"url":"https://dealer.example/start"}]}')
            if url.endswith("/start"):
                return FetchResult(url, 302, {"location": "/car/1"}, b"")
            raise AssertionError("Inventory boundary escaped before physical request")
        with patch("discovery.transport._resolve", return_value=["93.184.216.34"]), patch("discovery.transport._request", side_effect=physical):
            run_worker(self.store, policy, max_tasks=2)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.store.budget_status()[0]["spent"], 4)
        self.assertFalse(any(url.endswith("/car/1") for url in calls))
        self.assertTrue(any(task["status"] == "blocked" for task in self.store.tasks()))

    def test_additive_migration_preserves_v1_state_and_marks_new_worker_contract(self):
        self.store.reserve_request("old:policy", self.binding["endpoint"], max_requests=10, min_interval=0)
        self.store.db.execute("PRAGMA user_version=1")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(self.store.budget_status(), [{"policy_ref": "old:policy", "spent": 1}])
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_known_revoked_policy_cannot_publish_local_replay(self):
        self.store.register_policy("test:admission", digest("fixture"))
        self.store.revoke_policy("test:admission", actor="test", reason="revoked")
        with self.assertRaises(ValueError):
            inspect_channel(self.store, b'{"results":[{"url":"https://dealer.example/"}]}', "application/json",
                self.binding["endpoint"], binding=self.binding, policy_ref="test:admission", observed_at=self.at, expires_at=self.expiry)
        self.assertEqual(self.store.count_candidates(), 0)

    def test_channel_redirect_is_blocked_before_unassessed_endpoint_fetch(self):
        self.store.enqueue(dict(task_key="redirect", plan_id="p", stratum="ES:t", country="ES", classes=["car"],
            strategy="synthetic", kind="query", locator="garage", priority=10, access_mode="manual_review",
            payload=dict(channel=self.binding, channel_checksum=digest(self.binding), channel_page=1)))
        policy = AccessPolicy(allowed_hosts=("search.example",), operator="test", policy_ref="test:admission",
            expires_at=self.expiry, min_interval_seconds=0)
        calls = []
        def physical(url, *_):
            calls.append(url)
            if url.endswith("/robots.txt"):
                return FetchResult(url, 404, {}, b"")
            if "/search?" in url:
                return FetchResult(url, 302, {"location": "/unassessed-api"}, b"")
            return FetchResult(url, 200, {"content-type": "application/json"}, b'{"results":[]}')
        with patch("discovery.transport._resolve", return_value=["93.184.216.34"]), patch("discovery.transport._request", side_effect=physical):
            run_worker(self.store, policy, max_tasks=1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.count_candidates(), 0)

    def test_invalid_channel_body_retains_partial_diagnostic_not_zero_results(self):
        self.store.enqueue(dict(task_key="badbody", plan_id="p", stratum="ES:t", country="ES", classes=["car"],
            strategy="synthetic", kind="query", locator="garage", priority=10, access_mode="manual_review",
            payload=dict(channel=self.binding, channel_checksum=digest(self.binding), channel_page=1)))
        policy = AccessPolicy(allowed_hosts=("search.example",), operator="test", policy_ref="test:admission",
            expires_at=self.expiry, min_interval_seconds=0)
        class Fake:
            def fetch(_, url):
                return FetchResult(url, 200, {"content-type": "application/json"}, b'not JSON')
        run_worker(self.store, policy, max_tasks=1, fetcher=Fake())
        summary = self.store.channel_summary(timestamp())
        self.assertEqual(summary["pages"], 1)
        self.assertEqual(summary["rows_unknown_pages"], 1)
        self.assertEqual(self.store.tasks()[0]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
