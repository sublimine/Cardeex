"""Finite planning, paging and country/locality fairness acceptance cases."""

from copy import deepcopy
import importlib.util
import tracemalloc
import unittest
from unittest.mock import patch


CLASSES = ["car", "lcv", "motorcycle", "motorhome"]


def profile(country="ES"):
    """A researched-profile-shaped synthetic input, never live geography."""
    return {
        "schema_version": "1.0.0", "country": country, "name": "Synthetic",
        "languages": ["xx"], "vehicle_classes": CLASSES[:],
        "geography": {"code_system": "synthetic", "unit_name": "locality",
                      "catalogue_url": "https://example.test/catalogue", "notes": "Fixture only"},
        "strategies": [{"id": name, "family": "local_search", "evidence_group": name,
                        "priority": priority, "access_mode": "manual_review", "source_type": "dealer_owned",
                        "seed_urls": [f"https://example.test/{name}"],
                        "query_templates": {"xx": ["{vehicle_term} {locality}"]},
                        "notes": "Fixture", "source_ids": ["fixture"], "vehicle_classes": CLASSES[:]}
                       for name, priority in (("dominant", 100), ("small", 1))],
        "vocabulary": {"xx": {kind: [kind, f"other-{kind}"] for kind in CLASSES}},
        "sources": [{"id": "fixture", "url": "https://example.test/catalogue", "title": "Fixture",
                     "checked_at": "2026-09-07", "access_notes": "Synthetic"}],
        "blind_spots": ["Synthetic geography"], "review_days": 30,
    }


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.planner"), "D3 planner absent")
        from discovery.planner import build_plan
        self.build = build_plan
        self.profiles = {c: profile(c) for c in ("ES", "FR")}
        self.localities = [{"country": c, "code": code, "name": f"Place {code}", "rural": code == "2"}
                           for c in self.profiles for code in ("1", "2")]

    def test_pages_resume_without_changing_identity_or_losing_work(self):
        full = self.build(self.profiles, self.localities, "first")
        tasks, offset = [], 0
        while True:
            page = self.build(self.profiles, self.localities, "first", max_tasks=7, offset=offset)
            self.assertEqual(page["plan_id"], full["plan_id"])
            tasks.extend(page["tasks"])
            if page["generation_complete"]:
                self.assertIsNone(page["next_offset"])
                break
            offset = page["next_offset"]
        self.assertEqual(tasks, full["tasks"])
        self.assertEqual(len(tasks), full["total_tasks"])
        self.assertEqual(len({task["task_key"] for task in tasks}), len(tasks))

    def test_identity_changes_only_with_semantic_inputs(self):
        base = self.build(self.profiles, self.localities, "first")
        reordered = self.build(dict(reversed(list(self.profiles.items()))), list(reversed(self.localities)), "first")
        self.assertEqual(base, reordered)
        changed = deepcopy(self.profiles)
        changed["ES"]["vocabulary"]["xx"]["car"].append("extra")
        self.assertNotEqual(base["plan_id"], self.build(changed, self.localities, "first")["plan_id"])
        self.assertNotEqual(base["plan_id"], self.build(self.profiles, self.localities, "second")["plan_id"])

    def test_shared_seed_is_not_repeated_per_locality_or_class(self):
        plan = self.build(self.profiles, self.localities, "first")
        seeds = [task for task in plan["tasks"] if task["kind"] == "fetch"]
        self.assertEqual(len(seeds), 4)
        self.assertTrue(all(set(task["classes"]) == set(CLASSES) for task in seeds))
        self.assertTrue(all(task["payload"]["locality_code"] is None for task in seeds))

    def test_low_priority_rural_and_each_country_receive_early_turns(self):
        plan = self.build(self.profiles, self.localities, "first")
        early = plan["tasks"][:12]
        self.assertEqual({task["country"] for task in early}, {"ES", "FR"})
        for country in self.profiles:
            self.assertEqual({task["payload"]["locality_code"] for task in early if task["country"] == country}, {None, "1", "2"})
        for country in self.profiles:
            for code in ("1", "2"):
                strategies = [task["strategy"] for task in early
                              if task["country"] == country and task["payload"]["locality_code"] == code]
                self.assertEqual(set(strategies), {"dominant", "small"})

    def test_missing_geography_is_visible_and_never_fabricated(self):
        plan = self.build(self.profiles, self.localities[:2], "first")
        self.assertEqual(plan["missing_geography"], ["FR"])
        self.assertEqual(plan["geography_scope"], "supplied_sample")
        self.assertIsNone(plan["geography_coverage_ratio"])
        self.assertEqual(len(plan["cells"]), 2 * 4 * 8)

    def test_future_country_is_plannable_without_engine_change(self):
        plan = self.build({"IT": profile("IT")}, [{"country": "IT", "code": "x", "name": "New place"}], "first")
        self.assertEqual({task["country"] for task in plan["tasks"]}, {"IT"})
        self.assertEqual(len(plan["cells"]), 32)

    def test_repeated_names_preserve_codes_and_region_in_query_context(self):
        localities = [{"country": "ES", "code": code, "name": "Same", "region": region}
                      for code, region in (("a", "North"), ("b", "South"))]
        tasks = self.build({"ES": profile()}, localities, "first")["tasks"]
        queries = [task for task in tasks if task["kind"] == "query"]
        self.assertTrue(any("North" in task["locator"] for task in queries))
        self.assertTrue(any("South" in task["locator"] for task in queries))
        self.assertEqual({task["payload"]["locality_code"] for task in queries}, {"a", "b"})
        self.assertTrue(all(task["payload"]["requires_human_execution"] for task in queries))

    def test_invalid_or_ambiguous_frontier_input_fails_before_planning(self):
        for kwargs in ({"max_tasks": 0}, {"max_tasks": True}, {"offset": -1}, {"offset": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.build(self.profiles, self.localities, "first", **kwargs)
        with self.assertRaises(ValueError):
            self.build(self.profiles, [self.localities[0], self.localities[0] | {"name": "Conflict"}], "first")
        with self.assertRaises(ValueError):
            self.build(self.profiles, [{"country": "IT", "code": "x", "name": "Unknown profile"}], "first")

    def test_template_without_vehicle_term_does_not_multiply_same_query(self):
        profiles = {"ES": profile()}
        profiles["ES"]["strategies"][0]["query_templates"] = {"xx": ["dealers {locality}"]}
        plan = self.build(profiles, self.localities[:2], "first")
        tasks = [task for task in plan["tasks"] if task["kind"] == "query" and task["strategy"] == "dominant"]
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(set(task["classes"]) == set(CLASSES) for task in tasks))

    def test_large_cartesian_frontier_materializes_only_requested_page(self):
        profiles = {"ES": profile()}
        profiles["ES"]["vocabulary"]["xx"] = {kind: [f"{kind}-{n}" for n in range(1000)] for kind in CLASSES}
        localities = [{"country": "ES", "code": str(n), "name": f"Place {n}"} for n in range(1000)]
        tracemalloc.start()
        try:
            plan = self.build(profiles, localities, "first", max_tasks=1)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(plan["total_tasks"], 8000002)
        self.assertEqual(len(plan["tasks"]), 1)
        self.assertLess(peak, 16 * 1024 * 1024, "planner expanded the Cartesian frontier")

    def test_frontier_ordinals_are_contiguous_across_pages(self):
        one = self.build(self.profiles, self.localities, "first", max_tasks=3)
        two = self.build(self.profiles, self.localities, "first", max_tasks=3, offset=3)
        self.assertEqual([task["payload"].get("planner_ordinal") for task in one["tasks"] + two["tasks"]], list(range(6)))

    def test_every_small_ragged_frontier_page_matches_full_fair_order(self):
        for size in (1, 2, 3):
            profiles = {country: profile(country) for country in ("ES", "IT")}
            for country, value in profiles.items():
                for index, strategy in enumerate(value["strategies"]):
                    strategy["seed_urls"] = [f"https://example.test/{strategy['id']}/{n}" for n in range(index + size)]
                    strategy["vehicle_classes"] = CLASSES[:index + 1]
                    strategy["query_templates"]["xx"].append("all dealers {locality}")
                value["vocabulary"]["xx"] = {kind: [f"{kind}-{n}" for n in range(index + size)]
                                               for index, kind in enumerate(CLASSES)}
            places = [{"country": "ES", "code": str(n), "name": f"Place {n}"} for n in range(size)]
            places.append({"country": "IT", "code": "single", "name": "Single place"})
            full = self.build(profiles, places, "ragged")
            for offset in range(full["total_tasks"] + 1):
                for page_size in (1, 3, 11):
                    with self.subTest(size=size, offset=offset, page_size=page_size):
                        page = self.build(profiles, places, "ragged", offset=offset, max_tasks=page_size)
                        self.assertEqual(page["tasks"], full["tasks"][offset:offset + page_size])

    def test_near_end_of_eight_million_tasks_constructs_only_requested_page(self):
        import discovery.planner as planner
        profiles = {"ES": profile()}
        profiles["ES"]["vocabulary"]["xx"] = {kind: [f"{kind}-{n}" for n in range(1000)] for kind in CLASSES}
        places = [{"country": "ES", "code": str(n), "name": f"Place {n}"} for n in range(1000)]
        actual_task = planner._task
        calls = 0
        def count_task(*args, **kwargs):
            nonlocal calls
            calls += 1
            self.assertLessEqual(calls, 3, "resume constructed skipped frontier tasks")
            return actual_task(*args, **kwargs)
        tracemalloc.start()
        try:
            with patch.object(planner, "_task", side_effect=count_task):
                page = self.build(profiles, places, "large", offset=7999999, max_tasks=3)
                end = self.build(profiles, places, "large", offset=8000002, max_tasks=3)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(calls, 3)
        self.assertEqual(len(page["tasks"]), 3)
        self.assertTrue(page["generation_complete"])
        self.assertEqual(end["tasks"], [])
        self.assertLess(peak, 16 * 1024 * 1024)

    def test_escaped_placeholder_does_not_multiply_literal_query(self):
        profiles = {"ES": profile()}
        profiles["ES"]["strategies"][0]["query_templates"] = {"xx": ["{{vehicle_term}} {locality}"]}
        plan = self.build(profiles, self.localities[:2], "escaped")
        queries = [task for task in plan["tasks"] if task["kind"] == "query" and task["strategy"] == "dominant"]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all("{vehicle_term}" in task["locator"] for task in queries))


if __name__ == "__main__":
    unittest.main()
