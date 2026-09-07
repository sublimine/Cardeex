"""Coverage counts evidence claims, never a query's intended geography."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

from test_planner import CLASSES, profile


NOW = "2026-09-07T12:00:00Z"


class EvidenceStore:
    """Read-protocol fixture: admitted observations and persistent work state."""
    def __init__(self, observations=(), tasks=(), plans=(), count=None):
        self.observations = list(observations)
        self.tasks = list(tasks)
        self.plans = list(plans)
        self.count = count if count is not None else len({r["candidate_id"] for r in self.observations})

    def iter_observations(self, now):
        return iter(self.observations)

    def iter_tasks(self):
        return iter(self.tasks)

    def list_plans(self):
        return self.plans

    def count_candidates(self):
        return self.count


def observation(candidate="one", **changes):
    value = {"candidate_id": candidate, "kind": "source", "decision_status": "unreviewed",
             "countries": ["ES"], "classes": ["car"], "source_type": "dealer_owned",
             "locality_code": "1", "locality_country": "ES", "evidence_group": "original", "observed_at": "2026-09-01T00:00:00Z"}
    value.update(changes)
    if not value["locality_code"]:
        value["locality_country"] = ""
    return value


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.coverage"), "D3 coverage absent")
        from discovery.coverage import coverage_report
        self.report = coverage_report
        self.profiles = {country: profile(country) for country in ("ES", "FR", "DE", "NL", "BE", "CH")}

    def cell(self, report, country="ES", kind="car", source_type="dealer_owned"):
        return next(cell for cell in report["cells"]
                    if (cell["market_country"], cell["vehicle_class"], cell["source_type"]) == (country, kind, source_type))

    def test_empty_store_renders_every_cell_with_unknown_denominator(self):
        report = self.report(EvidenceStore(), self.profiles, NOW)
        self.assertEqual(len(report["cells"]), 192)
        for cell in report["cells"]:
            self.assertIsNone(cell["coverage_ratio"])
            self.assertIsNone(cell["declared_comparable_units"])
            self.assertEqual(cell["candidates"], 0)
            self.assertEqual(cell["monitored_sources"], 0)
            self.assertEqual(cell["certified_sources"], 0)
        self.assertEqual(report["geography"]["countries_without_localities"], sorted(self.profiles))

    def test_repeat_evidence_does_not_double_count_candidates_or_entity_kinds(self):
        data = [observation(), observation(evidence_group="independent"),
                observation("pos", kind="point_of_sale"), observation("seller", kind="professional_seller")]
        report = self.report(EvidenceStore(data), self.profiles, NOW)
        cell = self.cell(report)
        self.assertEqual(cell["candidates"], 3)
        self.assertEqual(cell["candidate_kinds"]["source"], 1)
        self.assertEqual(cell["candidate_kinds"]["point_of_sale"], 1)
        self.assertEqual(cell["evidence_groups"], ["independent", "original"])
        self.assertEqual(cell["admitted_sources"], 0)

    def test_task_scope_never_fills_unknown_candidate_claims(self):
        from discovery.planner import build_plan
        plan = build_plan(self.profiles, [{"country": "ES", "code": "1", "name": "Here"}], "first")
        tasks = [task | {"status": "done"} for task in plan["tasks"]]
        data = [observation(countries=[], classes=[], locality_code=None, source_type="unknown")]
        report = self.report(EvidenceStore(data, tasks, [plan]), self.profiles, NOW)
        self.assertTrue(all(cell["candidates"] == 0 for cell in report["cells"]))
        self.assertEqual(report["unknown_claims"]["country"], 1)
        self.assertEqual(report["unknown_claims"]["vehicle_class"], 1)
        self.assertGreater(self.cell(report)["task_scope"]["done"], 0)

    def test_claims_from_different_evidence_do_not_form_invented_cross_products(self):
        data = [observation(classes=["car"]), observation(countries=["FR"], classes=["motorcycle"])]
        report = self.report(EvidenceStore(data), self.profiles, NOW)
        self.assertEqual(self.cell(report)["candidates"], 1)
        self.assertEqual(self.cell(report, "FR", "motorcycle")["candidates"], 1)
        self.assertEqual(self.cell(report, "ES", "motorcycle")["candidates"], 0)

    def test_decisions_are_not_admission_and_blocked_work_remains_visible(self):
        from discovery.planner import build_plan
        plan = build_plan(self.profiles, [{"country": "ES", "code": "1", "name": "Here"}], "first")
        task = next(task for task in plan["tasks"] if task["country"] == "ES" and task["kind"] == "query")
        task = task | {"status": "blocked", "reason": "permission_missing", "created_at": "2026-09-06T12:00:00Z"}
        report = self.report(EvidenceStore([observation(decision_status="accepted")], [task], [plan]), self.profiles, NOW)
        self.assertEqual(self.cell(report)["decisions"]["accepted"], 1)
        self.assertEqual(self.cell(report)["admitted_sources"], 0)
        self.assertEqual(report["work"]["blocked"], 1)
        self.assertEqual(report["work"]["human_required"], 1)
        self.assertEqual(report["work"]["oldest_pending_age_seconds"], 86400)

    def test_partial_plan_exposes_unplanned_and_untouched_localities(self):
        from discovery.planner import build_plan
        localities = [{"country": "ES", "code": str(n), "name": f"Place {n}"} for n in range(3)]
        plan = build_plan(self.profiles, localities, "first", max_tasks=1)
        report = self.report(EvidenceStore(tasks=plan["tasks"], plans=[plan]), self.profiles, NOW)
        self.assertEqual(report["geography"]["supplied_localities"], 3)
        self.assertEqual(report["geography"]["unplanned_localities"], 3)
        self.assertEqual(report["geography"]["untouched_localities"], 3)
        self.assertGreater(report["work"]["ungenerated_tasks"], 0)
        self.assertFalse(report["work"]["generation_complete"])

    def test_latest_plan_page_counts_unique_durable_tasks_and_retains_old_plans(self):
        from discovery.planner import build_plan
        first = build_plan({"ES": profile()}, [], "first", max_tasks=1)
        second = build_plan({"ES": profile()}, [], "first", max_tasks=1, offset=1)
        report = self.report(EvidenceStore(tasks=first["tasks"] + second["tasks"], plans=[first, second]), self.profiles, NOW)
        self.assertEqual(report["work"]["plans"], 1)
        self.assertEqual(report["work"]["ungenerated_tasks"], 0)
        self.assertTrue(report["work"]["generation_complete"])

    def test_now_requires_explicit_valid_utc(self):
        for value in ("2026-09-07", "2026-09-07T12:00:00", "2026-02-30T12:00:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.report(EvidenceStore(), self.profiles, value)

    def test_expanded_child_tasks_cannot_hide_ungenerated_plan_frontier(self):
        from discovery.planner import build_plan
        plan = build_plan({"ES": profile()}, [], "first", max_tasks=1)
        root = plan["tasks"][0]
        child = root | {"task_key": "child", "payload": root["payload"] | {"parent_task_key": root["task_key"]}}
        report = self.report(EvidenceStore(tasks=[root, child], plans=[plan]), self.profiles, NOW)
        self.assertEqual(report["work"]["tasks"], 2)
        self.assertEqual(report["work"]["ungenerated_tasks"], 1)
        self.assertFalse(report["work"]["generation_complete"])

    def test_real_store_round_trip_keeps_task_scope_apart_from_evidence(self):
        from discovery.model import Observation
        from discovery.planner import build_plan
        from discovery.store import Store
        plan = build_plan(self.profiles, [{"country": "ES", "code": "1", "name": "Here"}], "first", max_tasks=2)
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory) / "discovery.sqlite") as store:
                store.save_plan(plan)
                cid = store.ingest(Observation(
                    locator="https://garage.example/stock", kind="source", method="synthetic",
                    method_version="1", origin_locator="https://directory.example/item",
                    evidence_group="fixture", observed_at="2026-09-01T00:00:00Z",
                    expires_at="2026-10-01T00:00:00Z", policy_ref="synthetic",
                    countries=("FR",), classes=("motorcycle",), source_type="dealer_owned",
                    signals={"synthetic": True}))
                store.decide(cid, "accepted", actor="fixture", reason="Synthetic review",
                             expected_revision=1, now=NOW)
                with store.transaction():
                    report = self.report(store, self.profiles, NOW)
                self.assertEqual(report["registered_candidates"], 1)
                self.assertEqual(self.cell(report, "FR", "motorcycle")["candidates"], 1)
                self.assertEqual(self.cell(report)["candidates"], 0)
                self.assertEqual(report["work"]["tasks"], 2)
                self.assertEqual(report["work"]["ungenerated_tasks"], plan["total_tasks"] - 2)

    def test_unqualified_locality_with_several_country_claims_is_not_assigned(self):
        from discovery.planner import build_plan
        profiles = {country: profile(country) for country in ("ES", "FR")}
        places = [{"country": country, "code": "1", "name": country + " place"} for country in profiles]
        plan = build_plan(profiles, places, "first")
        rows = [observation(countries=["ES", "FR"], locality_code="1", locality_country="")]
        report = self.report(EvidenceStore(rows, plans=[plan]), profiles, NOW)
        self.assertFalse(any(place["candidate_evidence"] for place in report["geography"]["localities"]))
        self.assertEqual(report["geography"]["ambiguous_locality_evidence_candidates"], 1)

    def test_conflicting_geography_versions_preserve_work_context_and_ambiguity(self):
        from discovery.planner import build_plan
        profiles = {"ES": profile()}
        old = build_plan(profiles, [{"country": "ES", "code": "1", "name": "Old geography"}], "old")
        new = build_plan(profiles, [{"country": "ES", "code": "1", "name": "New geography"}], "new", max_tasks=1)
        tasks = [task | {"status": "done", "attempts": 1} for task in old["tasks"]]
        rows = [observation()]
        report = self.report(EvidenceStore(rows, tasks, [old, new]), profiles, NOW)
        reordered = self.report(EvidenceStore(rows, tasks, [new, old]), profiles, NOW)
        self.assertEqual(report, reordered)
        places = {place["name"]: place for place in report["geography"]["localities"]}
        self.assertEqual(set(places), {"Old geography", "New geography"})
        self.assertTrue(places["Old geography"]["attempted"])
        self.assertFalse(places["New geography"]["planned"])
        self.assertFalse(places["New geography"]["attempted"])
        self.assertTrue(all(place["definition_conflict"] for place in places.values()))
        self.assertFalse(any(place["candidate_evidence"] for place in places.values()))
        self.assertEqual(report["geography"]["conflicting_locality_codes"], 1)

    def test_market_country_never_substitutes_for_explicit_locality_country(self):
        from discovery.planner import build_plan
        profiles = {country: profile(country) for country in ("FR", "BE")}
        places = [{"country": country, "code": "1", "name": country + " place"} for country in profiles]
        plan = build_plan(profiles, places, "cross-border")
        rows = [observation(countries=["FR"], locality_code="1", locality_country="BE")]
        report = self.report(EvidenceStore(rows, plans=[plan]), profiles, NOW)
        geographic_rows = {row["country"]: row for row in report["geography"]["localities"]}
        self.assertTrue(geographic_rows["BE"]["candidate_evidence"])
        self.assertFalse(geographic_rows["FR"]["candidate_evidence"])
        self.assertEqual(self.cell(report, "FR")["candidates"], 1)
        self.assertEqual(self.cell(report, "BE")["candidates"], 0)

    def test_legacy_single_market_country_cannot_assign_unqualified_locality(self):
        from discovery.planner import build_plan
        profiles = {"ES": profile()}
        plan = build_plan(profiles, [{"country": "ES", "code": "1", "name": "Here"}], "legacy")
        row = observation()
        row.pop("locality_country")
        report = self.report(EvidenceStore([row], plans=[plan]), profiles, NOW)
        self.assertFalse(report["geography"]["localities"][0]["candidate_evidence"])
        self.assertEqual(report["geography"]["ambiguous_locality_evidence_candidates"], 1)
        self.assertEqual(report["unknown_claims"]["locality"], 1)


if __name__ == "__main__":
    unittest.main()
