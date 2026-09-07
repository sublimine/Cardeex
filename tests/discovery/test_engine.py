import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from discovery.store import Store


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.engine"), "D5 engine absent")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "discovery.db")
        self.addCleanup(self.store.close)
        self.now = datetime.now(timezone.utc)

    def task(self, **changes):
        task = dict(task_key="root", plan_id="p1", stratum="FR:rural", country="FR", classes=["car", "motorcycle"],
                    strategy="local", kind="fetch", locator="https://garage.example/", priority=10,
                    access_mode="public_web", payload={"evidence_group": "web:garage", "depth": 0})
        task.update(changes)
        return task

    def policy(self):
        from discovery.transport import AccessPolicy
        return AccessPolicy(allowed_hosts=("garage.example",), operator="test", policy_ref="test:policy",
                            expires_at=self.now + timedelta(days=1), min_interval_seconds=0)

    def fetcher(self, body, status=200, content_type="text/html"):
        from discovery.transport import FetchResult
        class FixtureFetcher:
            def __init__(self):
                self.called = []
            def fetch(self, url):
                self.called.append(url)
                return FetchResult(url, status, {"content-type": content_type}, body)
        return FixtureFetcher()

    def test_three_vehicle_dealer_retained_no_listing_details_fetched(self):
        from discovery.engine import run_worker
        body = b'<a href="/car/1">one</a><a href="/car/2">two</a><a href="/car/3">three</a>'
        fake = self.fetcher(body)
        self.store.enqueue(self.task())
        result = run_worker(self.store, self.policy(), max_tasks=1, fetcher=fake)
        self.assertEqual(result["tasks_processed"], 1)
        self.assertGreaterEqual(self.store.count_candidates(), 1)
        self.assertEqual(fake.called, ["https://garage.example/"])
        self.assertFalse(result["inventory_scraped"])

    def test_query_work_cannot_silently_execute_search_scraping(self):
        from discovery.engine import run_worker
        fake = self.fetcher(b"<html></html>")
        self.store.enqueue(self.task(kind="query", locator="garage Lyon"))
        run_worker(self.store, self.policy(), max_tasks=1, fetcher=fake)
        self.assertEqual(fake.called, [])
        self.assertEqual(self.store.tasks()[0]["reason"], "human_or_approved_search_provider_required")

    def test_search_scope_is_not_promoted_to_observed_country_or_class(self):
        from discovery.engine import run_worker
        fake = self.fetcher(b'<a href="/stock">Fahrzeuge</a>')
        self.store.enqueue(self.task())
        run_worker(self.store, self.policy(), max_tasks=1, fetcher=fake)
        for row in self.store.iter_observations(self.now.isoformat()):
            self.assertEqual(row["countries"], [])
            self.assertEqual(row["classes"], [])

    def test_http_error_keeps_unknown_and_blocks_not_done(self):
        from discovery.engine import run_worker
        self.store.enqueue(self.task())
        run_worker(self.store, self.policy(), max_tasks=1, fetcher=self.fetcher(b"Forbidden", status=403))
        self.assertEqual(self.store.count_candidates(), 0)
        self.assertEqual(self.store.tasks()[0]["status"], "blocked")

    def test_expansion_limit_preserves_candidates_and_marks_debt(self):
        from discovery.engine import run_worker
        self.store.enqueue(self.task())
        body = b'<a href="https://first.example/">partner one</a><a href="https://other.example/">partner two</a>'
        run_worker(self.store, self.policy(), max_tasks=1, max_children=1, fetcher=self.fetcher(body))
        self.assertEqual(next(t for t in self.store.iter_tasks() if t["task_key"] == "root")["status"], "partial")
        self.assertEqual(self.store.deferred_count(), 1)
        self.assertGreaterEqual(self.store.count_candidates(), 2)

    def test_manual_registry_access_never_auto_runs(self):
        from discovery.engine import run_worker
        self.store.enqueue(self.task(access_mode="permission_required"))
        fake = self.fetcher(b"<html></html>")
        run_worker(self.store, self.policy(), max_tasks=1, fetcher=fake)
        self.assertEqual(fake.called, [])

    def test_local_file_replay_preserves_original_bytes(self):
        from discovery.engine import inspect_document
        body = b'<a href="/stock">3 vehicles</a>'
        result = inspect_document(self.store, body, "text/html", "https://garage.example/", policy_ref="test:local",
                                  evidence_group="web:garage", observed_at=self.now.isoformat(),
                                  expires_at=(self.now + timedelta(days=1)).isoformat())
        self.assertEqual(self.store.artifact(result["body_sha256"]), body)
        repeat = inspect_document(self.store, body, "text/html", "https://garage.example/", policy_ref="test:local",
                                  evidence_group="web:garage", observed_at=self.now.isoformat(),
                                  expires_at=(self.now + timedelta(days=1)).isoformat())
        self.assertEqual(result["candidate_ids"], repeat["candidate_ids"])


if __name__ == "__main__":
    unittest.main()
