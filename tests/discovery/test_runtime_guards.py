"""Offline integration regressions: real SQLite registry, worker and fetcher."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from discovery import engine, transport
from discovery.store import Store
from discovery.transport import AccessPolicy, FetchResult


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "guards.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.now = datetime.now(timezone.utc)
        self.attempts = []

    def policy(self, **changes):
        values = dict(allowed_hosts=("a.example", "b.example"), operator="offline test",
                      policy_ref="fixture:guards", expires_at=self.now + timedelta(hours=1),
                      min_interval_seconds=0)
        values.update(changes)
        return AccessPolicy(**values)

    def task(self, key, locator):
        return dict(task_key=key, plan_id="review-plan", stratum="FR:rural", country="FR",
                    classes=["car"], strategy="local", kind="fetch", locator=locator,
                    priority=10, access_mode="public_web",
                    payload={"evidence_group": "web:review", "depth": 0})

    def response(self, url, status=200, body=b'<a href="/stock">Stock</a>', **headers):
        return FetchResult(url, status, {"content-type": "text/html", **headers}, body)

    @contextmanager
    def network(self, route):
        def request(url, address, timeout, limit):
            self.attempts.append(url)
            return self.response(url, 404, b"") if url.endswith("/robots.txt") else route(url)
        with patch.object(transport, "_resolve", return_value=["93.184.216.34"]), \
                patch.object(transport, "_request", request):
            yield

    def restart_store(self):
        self.store.close()
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def assert_no_discovery_commit(self):
        self.assertEqual(self.store.count_candidates(), 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
        self.assertEqual(self.store.tasks()[0]["status"], "blocked")

    def test_429_cooldown_survives_worker_and_database_restart(self):
        policy = self.policy()
        self.store.enqueue(self.task("A", "https://a.example/first"))
        self.store.enqueue(self.task("B", "https://a.example/second"))
        with self.network(lambda url: self.response(url, 429, **{"retry-after": "120"})):
            engine.run_worker(self.store, policy, max_tasks=1)
            self.restart_store()
            engine.run_worker(self.store, policy, max_tasks=1)
        self.assertEqual(self.attempts, ["https://a.example/robots.txt", "https://a.example/first"])
        self.assertGreater(self.store.origin_ready_at("https://a.example/second"), time.time() + 100)

    def test_redirected_429_cools_actual_target_host_after_restart(self):
        policy = self.policy()
        self.store.enqueue(self.task("A", "https://a.example/first"))
        self.store.enqueue(self.task("B", "https://b.example/second"))
        def route(url):
            if url == "https://a.example/first":
                return self.response(url, 302, location="https://b.example/first")
            return self.response(url, 429, **{"retry-after": "120"})
        with self.network(route):
            engine.run_worker(self.store, policy, max_tasks=1)
            self.restart_store()
            engine.run_worker(self.store, policy, max_tasks=1)
        self.assertEqual(self.attempts, ["https://a.example/robots.txt", "https://a.example/first",
                                        "https://b.example/robots.txt", "https://b.example/first"])
        self.assertGreater(self.store.origin_ready_at("https://b.example/second"), time.time() + 100)

    def test_deferred_overlap_with_already_queued_locator_releases_safely(self):
        first = self.task("A", "https://a.example/one")
        second = self.task("B", "https://a.example/two")
        row = dict(locator="https://a.example/stock", kind="source",
                   relation="inventory_surface", label="Stock", signals={})
        child_a = engine.expansion_tasks(first, [row])[0]
        child_b = engine.expansion_tasks(second, [row])[0]
        self.assertEqual(child_a["task_key"], child_b["task_key"])
        self.store.enqueue(first)
        lease = self.store.claim("offline review")
        self.store.commit_result("A", lease["lease_token"], [], status="partial",
                                 reason="child_budget", deferred_tasks=[child_a])
        self.store.enqueue(child_b)
        self.restart_store()
        self.assertEqual(self.store.release_deferred(), 1)
        self.assertEqual(self.store.deferred_count(), 0)
        self.assertEqual(len(self.store.tasks()), 2)

    def test_worker_deadline_refuses_throttle_beyond_remaining_budget(self):
        self.store.enqueue(self.task("A", "https://a.example/first"))
        with self.network(lambda url: self.response(url)), \
                patch.object(transport.time, "sleep", side_effect=AssertionError("Must not oversleep budget")):
            result = engine.run_worker(self.store, self.policy(min_interval_seconds=10),
                                       max_tasks=1, max_seconds=1)
        self.assertEqual(self.attempts, ["https://a.example/robots.txt"])
        self.assertEqual(result["outcomes"], {"partial": 1})
        self.assertEqual(self.store.count_candidates(), 0)

    def test_policy_expiry_during_real_parser_prevents_commit(self):
        self.store.enqueue(self.task("A", "https://a.example/first"))
        clock = [self.now]
        real_parser = engine.parse_document
        class StageClock:
            @staticmethod
            def now(tz=None):
                return clock[0]
        def parse_then_advance(*args):
            rows = real_parser(*args)
            clock[0] = self.now + timedelta(hours=2)
            return rows
        with self.network(lambda url: self.response(url)), \
                patch.object(engine, "datetime", StageClock), \
                patch.object(engine, "parse_document", parse_then_advance):
            engine.run_worker(self.store, self.policy(), max_tasks=1)
        self.assert_no_discovery_commit()

    def test_policy_revocation_during_fetch_prevents_commit(self):
        policy = self.policy()
        self.store.enqueue(self.task("A", "https://a.example/first"))
        def revoke_during_response(url):
            self.store.revoke_policy(policy.policy_ref, actor="offline test", reason="withdrawn")
            return self.response(url)
        with self.network(revoke_during_response):
            engine.run_worker(self.store, policy, max_tasks=1)
        self.assert_no_discovery_commit()


if __name__ == "__main__":
    unittest.main()
