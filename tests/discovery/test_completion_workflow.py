"""Local cross-component workflows; every catalogue/capture/identity is synthetic.

These tests traverse the real planner, registry, worker, review, handoff and
calendar. The only replacement is the physical fetch boundary. They establish
software behavior, never national data quality, permissions or coverage.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from discovery.channels import RDW_ENDPOINT
from discovery.coverage import coverage_report
from discovery.engine import run_worker
from discovery.estimation import estimate_stratum
from discovery.geography import catalogue_localities, load_catalogue
from discovery.handoff import handoff_records
from discovery.model import canonical_json, timestamp
from discovery.planner import build_plan
from discovery.profiles import load_profiles
from discovery.scheduling import inspect_schedules, register_schedule, tick
from discovery.store import Store
from discovery.transport import AccessPolicy, FetchResult


class SyntheticChannelFetcher:
    def __init__(self, observed_at, on_fetch=None):
        self.calls = []
        self.on_fetch = on_fetch
        self.responses = {
            "overpass.example": canonical_json({"version": 0.6,
                "osm3s": {"timestamp_osm_base": observed_at}, "elements": [
                    {"type": "node", "id": 701, "lat": 52.01, "lon": 4.31,
                     "tags": {"shop": "car", "name": "Synthetic rural garage",
                              "addr:city": "Fixture village", "addr:country": "NL"}}
                ]}).encode(),
            "opendata.rdw.nl": canonical_json([
                {"volgnummer": "37", "naam_bedrijf": "Synthetic recognised workshop",
                 "straat": "Fixture street", "huisnummer": "1", "plaats": "Fixture village"}
            ]).encode(),
        }

    def fetch(self, url):
        self.calls.append(url)
        if self.on_fetch:
            self.on_fetch(url)
        host = urlsplit(url).hostname
        if host not in self.responses:
            raise AssertionError("Unexpected source or registry fetch in synthetic workflow")
        return FetchResult(url, 200, {"content-type": "application/json"}, self.responses[host])


class CompletionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cardeex-completion-workflow-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "discovery.sqlite"
        self.now = datetime.now(timezone.utc)
        self.source_at = timestamp((self.now - timedelta(days=1)).isoformat())
        self.expires_at = timestamp((self.now + timedelta(days=14)).isoformat())
        self.assessment = "fixture:reviewed-discovery-scope"
        # Any accidental escape from the supplied fetcher fails before DNS/I/O.
        network = patch("discovery.transport._resolve", side_effect=AssertionError("Unexpected network access"))
        network.start()
        self.addCleanup(network.stop)

    def planning_inputs(self, adapters=("overpass_json", "rdw_socrata")):
        body = canonical_json({"units": [{"code": "GM0001", "name": "Fixture village",
            "region": "Fixture region", "rural": True, "aliases": ["Fixture alias"]}]}).encode()
        catalogue = load_catalogue(body, {"country": "NL", "version": "synthetic-v1",
            "source_url": "https://catalogue.example/synthetic-municipalities.json",
            "observed_at": self.source_at, "body_sha256": hashlib.sha256(body).hexdigest()})
        self.assertEqual(catalogue["completeness"]["status"], "unknown")
        localities = catalogue_localities(catalogue)
        self.assertEqual(localities[0]["code"], "GM0001")
        profile = load_profiles()["NL"]
        strategies = {"overpass_json": "nl_cbs_local_extract", "rdw_socrata": "nl_rdw_recognised"}
        selected = {strategies[adapter] for adapter in adapters}
        profile["strategies"] = [strategy for strategy in profile["strategies"] if strategy["id"] in selected]
        channels = {}
        for adapter in adapters:
            channels[strategies[adapter]] = {"adapter": adapter,
                "endpoint": "https://overpass.example/api/interpreter" if adapter == "overpass_json" else RDW_ENDPOINT,
                "assessment_ref": self.assessment, "countries": ["NL"],
                "vehicle_classes": list(profile["vehicle_classes"]), "max_pages": 2,
                "parameters": {"bbox": [52.0, 4.3, 52.1, 4.4], "row_limit": 10,
                               "tags": [{"key": "shop", "value": "car"}]}
                              if adapter == "overpass_json" else {"page_size": 10}}
        return {"NL": profile}, localities, channels

    def policy(self):
        return AccessPolicy(allowed_hosts=("overpass.example", "opendata.rdw.nl"), operator="fixture:operator",
                            policy_ref=self.assessment, expires_at=self.expires_at,
                            min_interval_seconds=0, max_requests=40)

    def populate(self, store, inputs, epoch="fixture:first", fetcher=None):
        profiles, localities, channels = inputs
        plan = build_plan(profiles, localities, epoch, channels=channels, max_tasks=1000)
        self.assertTrue(plan["generation_complete"])
        self.assertEqual(plan["geography_scope"], "supplied_sample")
        store.save_plan(plan)
        fetcher = fetcher or SyntheticChannelFetcher(self.source_at)
        result = run_worker(store, self.policy(), fetcher=fetcher, max_tasks=1000)
        self.assertFalse(result["inventory_scraped"])
        self.assertIsNone(result["coverage_ratio"])
        return plan, fetcher, result

    def accept_and_handoff(self, store):
        candidates = store.handoff()["candidates"]
        self.assertTrue(candidates, "The real channel-to-registry path produced no candidates")
        for record in candidates:
            candidate = store.candidate(record["candidate_id"])
            store.decide(record["candidate_id"], "accepted", actor="fixture:reviewer",
                         reason="Synthetic discovery candidate reviewed; no inventory admission",
                         expected_revision=candidate["revision"])
        records = list(handoff_records(store))
        header, footer = records[0], records[-1]
        self.assertFalse(header["acquire_inventory"])
        self.assertIsNone(header["coverage_ratio"])
        middle = [r for r in records if r["record_type"] in ("discovery_handoff_candidate", "discovery_handoff_blocked")]
        checksum = hashlib.sha256("".join(canonical_json(row) + "\n" for row in middle).encode()).hexdigest()
        self.assertEqual(footer["candidate_and_blocked_records_sha256"], checksum)
        return [r for r in records if r["record_type"] == "discovery_handoff_candidate"]

    def test_catalogue_to_handoff_then_restart_and_scheduled_refresh_preserve_evidence(self):
        inputs = self.planning_inputs()
        with Store(self.path) as store:
            first_plan, fetcher, _ = self.populate(store, inputs)
            self.assertEqual({urlsplit(url).hostname for url in fetcher.calls}, {"overpass.example", "opendata.rdw.nl"})
            exported = self.accept_and_handoff(store)
            seller = next(row for row in exported if row["candidate"]["claimed_kind"] == "professional_seller")
            candidate_id = seller["candidate"]["candidate_id"]
            self.assertEqual(seller["candidate"]["observed_locator"], "https://www.openstreetmap.org/node/701")
            signals = seller["evidence"][0]["claims"]["signals"]
            self.assertEqual(signals["locator_role"], "registry_record")
            self.assertEqual(signals.get("website_status"), "not_published")
            self.assertFalse(signals["discovery_fetch_allowed"])
            self.assertEqual(signals["osm_tags"]["name"], "Synthetic rural garage")
            self.assertEqual(seller["candidate"]["claimed_countries"], [])
            self.assertEqual(seller["candidate"]["suspected_classes"], [])
            prior_evidence = {row["evidence_id"] for row in store.candidate(candidate_id)["evidence"]}
            store.reserve_request(self.assessment, "https://overpass.example/", max_requests=40, min_interval=0)
            spec = {"schedule_id": "fixture:refresh", "profiles": inputs[0], "localities": inputs[1],
                    "channels": inputs[2], "starts_at": timestamp(self.now.isoformat()), "interval_seconds": 86400}
            register_schedule(store, spec, actor="fixture:operator")
        # The context manager releases the actual Windows database handle before reopening.
        with Store(self.path) as reopened:
            self.assertEqual(reopened.candidate(candidate_id)["decision"]["status"], "accepted")
            self.assertEqual(prior_evidence, {row["evidence_id"] for row in reopened.candidate(candidate_id)["evidence"]})
            generated = tick(reopened, now=timestamp(self.now.isoformat()), max_tasks=1000)
            self.assertTrue(generated["pages"])
            self.assertNotEqual(generated["pages"][0]["plan_id"], first_plan["plan_id"])
            fetcher = SyntheticChannelFetcher(self.source_at)
            run_worker(reopened, self.policy(), fetcher=fetcher, max_tasks=1000)
            self.assertEqual(len(fetcher.calls), 2)
            after = reopened.candidate(candidate_id)
            self.assertTrue(prior_evidence < {row["evidence_id"] for row in after["evidence"]})
            self.assertEqual(after["decision"]["status"], "accepted")
            self.assertEqual(reopened.budget_status()[0]["spent"], 1)
            report = coverage_report(reopened, inputs[0], now=timestamp())
            self.assertIsNone(report["coverage_ratio"])
            self.assertIsNone(report["geography"]["coverage_ratio"])
            self.assertTrue(all(cell["certified_sources"] == 0 for cell in report["cells"]))
        generations = inspect_schedules(self.path)["generations"]
        self.assertEqual(len(generations), 1)
        self.assertTrue(generations[0]["generation_complete"])

    def test_suppression_survives_restart_and_identical_channel_recapture(self):
        inputs = self.planning_inputs(("overpass_json",))
        with Store(self.path) as store:
            self.populate(store, inputs)
            row = self.accept_and_handoff(store)[0]
            candidate_id = row["candidate"]["candidate_id"]
            store.suppress(candidate_id, actor="fixture:operator", reason="Synthetic deletion request")
        with Store(self.path) as reopened:
            _, fetcher, outcome = self.populate(reopened, inputs, epoch="fixture:after-suppression")
            self.assertEqual(len(fetcher.calls), 1)
            self.assertTrue(outcome["outcomes"].get("blocked", 0))
            candidate = reopened.candidate(candidate_id)
            self.assertTrue(candidate["suppressed"])
            self.assertEqual(candidate["evidence"], [])
            records = list(handoff_records(reopened))
            self.assertFalse(any(row["record_type"] == "discovery_handoff_candidate" for row in records))
            self.assertTrue(any(row["record_type"] == "discovery_deletion_receipt" and
                                row["candidate_id"] == candidate_id for row in records))

    def test_revocation_during_admitted_channel_fetch_prevents_result_publication(self):
        inputs = self.planning_inputs(("overpass_json",))
        with Store(self.path) as store:
            def revoke(_url):
                store.revoke_policy(self.assessment, actor="fixture:reviewer", reason="Revoked during synthetic fetch")
            fetcher = SyntheticChannelFetcher(self.source_at, on_fetch=revoke)
            _, _, outcome = self.populate(store, inputs, fetcher=fetcher)
            self.assertEqual(len(fetcher.calls), 1, "A valid binding must reach the fetch boundary before revocation")
            self.assertTrue(outcome["outcomes"].get("blocked", 0))
            self.assertEqual(store.count_candidates(), 0)
            self.assertEqual(store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
        with Store(self.path) as reopened:
            self.assertEqual(reopened.count_candidates(), 0)
            self.assertTrue(any(item["revoked"] for item in reopened.db.execute("SELECT revoked FROM policies")))

    def test_rdw_cursor_and_empty_terminal_page_survive_worker_restart(self):
        profiles, localities, channels = self.planning_inputs(("rdw_socrata",))
        channels["nl_rdw_recognised"]["parameters"]["page_size"] = 1
        fixture = SyntheticChannelFetcher(self.source_at)
        first_body = fixture.responses["opendata.rdw.nl"]

        class CursorFetcher:
            def __init__(self):
                self.predicates = []

            def fetch(self, url):
                predicate = parse_qs(urlsplit(url).query)["$where"][0]
                self.predicates.append(predicate)
                if predicate == "volgnummer > 0":
                    body = first_body
                elif predicate == "volgnummer > 37":
                    body = b"[]"
                else:
                    raise AssertionError("The worker lost or changed its persisted RDW cursor")
                return FetchResult(url, 200, {"content-type": "application/json"}, body)

        with Store(self.path) as store:
            # Materialize just the source seed. Query proposals remain declared
            # planning debt; their fair queue order must not decide this restart.
            plan = build_plan(profiles, localities, "fixture:rdw-paging", channels=channels, max_tasks=1)
            self.assertFalse(plan["generation_complete"])
            store.save_plan(plan)
            first = CursorFetcher()
            run_worker(store, self.policy(), fetcher=first, max_tasks=1)
            self.assertEqual(first.predicates, ["volgnummer > 0"])
            self.assertEqual(store.count_candidates(), 1)
            summary = store.channel_summary(timestamp())
            self.assertEqual(summary["pages_with_continuation"], 1)
            candidate_id = store.handoff()["candidates"][0]["candidate_id"]
        with Store(self.path) as reopened:
            second = CursorFetcher()
            run_worker(reopened, self.policy(), fetcher=second, max_tasks=1000)
            self.assertEqual(second.predicates, ["volgnummer > 37"])
            summary = reopened.channel_summary(timestamp())
            self.assertEqual(summary["pages"], 2)
            self.assertEqual(summary["rows"], 1)
            self.assertEqual(summary["complete_query_pages"], 1)
            self.assertIsNone(summary["coverage_ratio"])
            self.assertEqual(reopened.count_candidates(), 1)
            self.assertTrue(reopened.candidate(candidate_id)["evidence"])
            checksum = hashlib.sha256(first_body).hexdigest()
            self.assertEqual(reopened.artifact(checksum), first_body)

    def test_accepted_candidates_do_not_supply_resolved_population_or_certified_coverage(self):
        inputs = self.planning_inputs()
        with Store(self.path) as store:
            self.populate(store, inputs)
            candidates = self.accept_and_handoff(store)
        as_of = timestamp((self.now + timedelta(days=1)).isoformat())
        stratum = {"market_country": "NL", "vehicle_class": "car", "source_type": "dealer_owned",
            "entity_kind": "professional_seller", "universe_version": "synthetic-study-v1",
            "window_start": self.source_at, "window_end": as_of}
        sources = sorted({row["evidence"][0]["claims"]["evidence_group"] for row in candidates})
        self.assertEqual(len(sources), 2)
        groups = [{"id": "synthetic-group-" + str(index), "sources": [source], "independent": True,
                   "evidence_ref": "fixture:conditional-independence-" + str(index),
                   "observed_at": self.source_at, "expires_at": self.expires_at}
                  for index, source in enumerate(sources)]
        unresolved = [{"entity_id": row["candidate"]["candidate_id"], "identity_status": "unresolved",
                       "review_status": row["candidate"]["decision"]["status"],
                       "source_id": row["evidence"][0]["claims"]["evidence_group"],
                       "evidence_ref": row["evidence"][0]["evidence_id"],
                       "observed_at": row["evidence"][0]["observed_at"],
                       "expires_at": row["evidence"][0]["expires_at"], "stratum": stratum}
                      for row in candidates]
        unknown = estimate_stratum(unresolved, independent_groups=groups, now=as_of, stratum=stratum)
        self.assertEqual(unknown["status"], "unknown")
        self.assertIn("unresolved_identity", unknown["reasons"])
        self.assertIsNone(unknown["estimate"])
        # A separate, explicitly synthetic resolved population tests conditional arithmetic.
        synthetic = [{"entity_id": "fixture:resolved-" + str(identity), "identity_status": "resolved",
                      "review_status": "accepted", "source_id": source,
                      "evidence_ref": f"fixture:capture:{index}:{identity}", "observed_at": self.source_at,
                      "expires_at": self.expires_at, "stratum": stratum}
                     for index, source in enumerate(sources)
                     for identity in (range(60) if index == 0 else range(40, 100))]
        conditional = estimate_stratum(synthetic, independent_groups=groups, now=as_of, stratum=stratum)
        self.assertEqual(conditional["status"], "estimated")
        self.assertEqual(conditional["observed"], 100)
        self.assertEqual(conditional["overlap"], 20)
        self.assertGreater(conditional["estimate"], conditional["observed"])
        self.assertIsNone(conditional["coverage_ratio"])
        self.assertIn("exploratory_model_not_coverage_certification", conditional["limitations"])
        dependent = deepcopy(groups)
        dependent[1]["depends_on"] = [dependent[0]["id"]]
        withdrawn = estimate_stratum(synthetic, independent_groups=dependent, now=as_of, stratum=stratum)
        self.assertEqual(withdrawn["status"], "unknown")
        self.assertIsNone(withdrawn["estimate"])


if __name__ == "__main__":
    unittest.main()
