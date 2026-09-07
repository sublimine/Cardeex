"""Admitted channel-to-ledger-to-handoff integration with synthetic transport."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from discovery.engine import run_worker
from discovery.model import digest
from discovery.planner import build_plan
from discovery.profiles import load_profiles
from discovery.store import Store
from discovery.transport import AccessPolicy, FetchResult


class ChannelEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "state.sqlite")
        self.addCleanup(lambda: self.store.close())
        self.now = datetime.now(timezone.utc)
        self.binding = dict(adapter="searxng_json", endpoint="https://search.example/search",
                            assessment_ref="assessment:fixture", countries=["ES"],
                            vehicle_classes=["car", "lcv", "motorcycle", "motorhome"], parameters={}, max_pages=1)
        self.policy = AccessPolicy(allowed_hosts=("search.example", "dealer.example"), operator="test",
                                   policy_ref="assessment:fixture", expires_at=self.now + timedelta(days=1),
                                   min_interval_seconds=0)

    def query(self, binding=None):
        from discovery.channels import validate_binding
        binding = validate_binding(binding or self.binding)
        return dict(task_key="query", plan_id="p1", stratum="ES:fixture", country="ES", classes=["car"],
                    strategy="es_local_directories", kind="query", locator="garage Example", priority=20,
                    access_mode="manual_review", payload=dict(evidence_group="search:search.example", depth=0,
                    channel=binding, channel_checksum=digest(binding), channel_page=1))

    def fake(self, responder):
        class Fetcher:
            def __init__(self):
                self.called = []
            def fetch(inner, url):
                inner.called.append(url)
                body, mime = responder(url)
                return FetchResult(url, 200, {"content-type": mime}, body)
        return Fetcher()

    def test_plan_binding_is_part_of_identity_and_search_provenance(self):
        profiles = {"ES": load_profiles()["ES"]}
        localities = [{"country": "ES", "code": "00001", "name": "Example"}]
        original = build_plan(profiles, localities, "fixture")
        bound = build_plan(profiles, localities, "fixture", channels={"es_local_directories": self.binding})
        self.assertNotEqual(original["plan_id"], bound["plan_id"])
        queries = [t for t in bound["tasks"] if t["strategy"] == "es_local_directories" and t["kind"] == "query"]
        self.assertTrue(queries)
        self.assertTrue(all(t["payload"]["channel_checksum"] for t in queries))
        self.assertTrue(all(t["payload"]["evidence_group"] == "channel:searxng_json:search.example" for t in queries))
        with self.assertRaises(ValueError):
            build_plan(profiles, localities, "fixture", channels={"absent_strategy": self.binding})

    def test_empty_catalogue_cannot_escape_selected_country(self):
        import hashlib
        from discovery.geography import load_catalogue
        body = b'{"units":[]}'
        catalogue = load_catalogue(body, dict(country="FR", version="synthetic", source_url="https://geo.example/data",
            observed_at="2026-09-01T00:00:00Z", body_sha256=hashlib.sha256(body).hexdigest()))
        with self.assertRaises(ValueError):
            build_plan({"ES": load_profiles()["ES"]}, [], "fixture", catalogues=[catalogue])

    def test_search_fetches_provider_then_owned_site_without_inheriting_channel(self):
        self.store.enqueue(self.query())
        def responder(url):
            if url.startswith("https://search.example/"):
                return json.dumps({"results": [{"url": "https://dealer.example/", "title": "Synthetic garage"}]}).encode(), "application/json"
            return b'<meta name="generator" content="WordPress"><a href="/stock">Stock</a>', "text/html"
        fake = self.fake(responder)
        result = run_worker(self.store, self.policy, max_tasks=2, fetcher=fake)
        self.assertEqual(len(fake.called), 2)
        self.assertTrue(fake.called[0].startswith("https://search.example/search?q="))
        self.assertEqual(fake.called[1], "https://dealer.example/")
        child = next(t for t in self.store.tasks() if t["locator"] == "https://dealer.example/")
        self.assertNotIn("channel", child["payload"])
        self.assertGreaterEqual(result["deferred_frontier"], 1)
        evidence = list(self.store.iter_observations(datetime.now(timezone.utc).isoformat()))
        self.assertTrue(evidence)
        self.assertTrue(all(not e["countries"] and not e["classes"] for e in evidence))

    def test_assessment_scope_and_checksum_are_rechecked_before_network(self):
        for alteration in ("assessment", "country", "checksum"):
            task = self.query()
            task["task_key"] = alteration
            if alteration == "assessment":
                task["payload"]["channel"]["assessment_ref"] = "other"
                task["payload"]["channel_checksum"] = digest(task["payload"]["channel"])
            elif alteration == "country":
                task["country"] = "FR"
            else:
                task["payload"]["channel_checksum"] = "0" * 64
            self.store.enqueue(task)
        fake = self.fake(lambda _: (b'{"results":[]}', "application/json"))
        run_worker(self.store, self.policy, max_tasks=3, fetcher=fake)
        self.assertEqual(fake.called, [])
        self.assertTrue(all(t["status"] == "blocked" for t in self.store.tasks()))

    def test_rdw_pages_survive_restart_and_keep_web_less_records(self):
        from discovery.channels import validate_binding
        binding = validate_binding(dict(self.binding, adapter="rdw_socrata", countries=["NL"],
                         endpoint="https://opendata.rdw.nl/resource/5k74-3jha.json", parameters={"page_size": 1}, max_pages=2))
        task = self.query(binding)
        task.update(kind="fetch", country="NL", locator=binding["endpoint"], access_mode="open_data")
        self.store.enqueue(task)
        policy = AccessPolicy(allowed_hosts=("opendata.rdw.nl",), operator="test", policy_ref=self.policy.policy_ref,
                              expires_at=self.policy.expires_at, min_interval_seconds=0)
        fake = self.fake(lambda url: (b'[{"volgnummer":"1","naam_bedrijf":"Synthetic garage"}]' if "where=volgnummer+%3E+0" in url else b'[]', "application/json"))
        run_worker(self.store, policy, max_tasks=1, fetcher=fake)
        path = self.store.path
        self.store.close()
        self.store = Store(path)
        run_worker(self.store, policy, max_tasks=1, fetcher=fake)
        self.assertEqual(len(fake.called), 2)
        self.assertTrue(any(r["locator"].endswith("#rdw=1") for r in self.store.iter_observations(datetime.now(timezone.utc).isoformat())))
        self.assertEqual(self.store.deferred_count(), 0)

    def test_channel_detail_result_stays_evidence_without_detail_fetch(self):
        self.store.enqueue(self.query())
        fake = self.fake(lambda _: (b'{"results":[{"url":"https://dealer.example/car/1"}]}', "application/json"))
        run_worker(self.store, self.policy, max_tasks=3, fetcher=fake)
        self.assertEqual(len(fake.called), 1)
        self.assertFalse(any(t["locator"] == "https://dealer.example/car/1" for t in self.store.tasks()))


if __name__ == "__main__":
    unittest.main()
