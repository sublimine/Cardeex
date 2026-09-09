"""Own synthetic capture experiments; estimates never certify market coverage."""

import copy
import importlib.util
import random
import unittest


NOW = "2026-09-07T12:00:00Z"
STRATUM = {"market_country": "ES", "vehicle_class": "car", "source_type": "dealer_owned",
           "entity_kind": "point_of_sale", "universe_version": "synthetic-1",
           "window_start": "2026-09-01T00:00:00Z", "window_end": "2026-09-07T00:00:00Z"}
GROUPS = [{"id": group, "sources": [group], "independent": True,
           "evidence_ref": "synthetic-design:" + group,
           "observed_at": "2026-09-01T00:00:00Z", "expires_at": "2026-10-01T00:00:00Z"}
          for group in ("a", "b")]


def records(left, right):
    return [{"entity_id": str(entity), "source_id": source, "identity_status": "resolved",
             "review_status": "accepted", "evidence_ref": f"synthetic:{source}:{entity}",
             "observed_at": "2026-09-02T00:00:00Z", "expires_at": "2026-10-01T00:00:00Z",
             "stratum": dict(STRATUM)}
            for source, members in (("a", left), ("b", right)) for entity in members]


class EstimationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.estimation"), "R3 estimation module absent")
        from discovery.estimation import estimate_stratum
        self.estimate = estimate_stratum
        # Finite N=1000 design with n1=400, n2=500, overlap=200.
        self.data = records(range(400), list(range(200)) + list(range(400, 700)))

    def run_estimate(self, data=None, groups=None, stratum=None):
        return self.estimate(self.data if data is None else data,
                             independent_groups=GROUPS if groups is None else groups,
                             now=NOW, stratum=STRATUM if stratum is None else stratum)

    def test_known_population_and_distinct_observed_units(self):
        result = self.run_estimate()
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["observed"], 700)
        self.assertAlmostEqual(result["estimate"], 998.5074626865671)
        self.assertLessEqual(result["interval"]["lower"], 1000)
        self.assertGreaterEqual(result["interval"]["upper"], 1000)
        self.assertEqual(result["interval"]["method"], "chapman_normal_approximation")
        self.assertIsNone(result["coverage_ratio"])
        self.assertIn("not_calibrated_on_real_dealers", result["limitations"])

    def test_duplicate_evidence_or_source_family_members_never_increase_units(self):
        groups = copy.deepcopy(GROUPS)
        groups[0]["sources"].append("a-copy")
        duplicate = [row | {"source_id": "a-copy"} for row in self.data if row["source_id"] == "a"]
        result = self.run_estimate(self.data * 2 + duplicate, groups)
        self.assertEqual(result["observed"], 700)
        self.assertEqual(result["estimate"], self.run_estimate()["estimate"])

    def test_unknown_for_single_source_dependent_sources_or_no_overlap(self):
        dependent = copy.deepcopy(GROUPS)
        dependent[1]["sources"] = ["a"]
        cases = [(self.data, GROUPS[:1], "two_independent_groups_required"),
                 (self.data, dependent, "shared_source_between_groups"),
                 (records(range(100), range(100, 200)), GROUPS, "no_overlap")]
        for data, groups, reason in cases:
            with self.subTest(reason=reason):
                result = self.run_estimate(data, groups)
                self.assertEqual(result["status"], "unknown")
                self.assertIn(reason, result["reasons"])
                self.assertIsNone(result["estimate"])
                self.assertIsNone(result["coverage_ratio"])

    def test_expired_future_unreviewed_and_unresolved_rows_block_estimation(self):
        for changes, reason in [({"expires_at": NOW}, "expired_capture"),
                                ({"observed_at": "2027-01-01T00:00:00Z"}, "invalid_capture_window"),
                                ({"review_status": "unreviewed"}, "unreviewed_capture"),
                                ({"identity_status": "candidate"}, "unresolved_identity"),
                                ({"evidence_ref": ""}, "missing_capture_evidence")]:
            with self.subTest(changes=changes):
                result = self.run_estimate(self.data + [self.data[0] | changes])
                self.assertEqual(result["status"], "unknown")
                self.assertIn(reason, result["reasons"])

    def test_no_cross_stratum_or_unqualified_country_pooling(self):
        bad = self.data[0] | {"stratum": STRATUM | {"market_country": "FR"}}
        result = self.run_estimate([bad] + self.data)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("stratum_mismatch", result["reasons"])

    def test_independence_needs_live_evidence_not_two_labels(self):
        for changes, reason in [({"independent": False}, "independence_not_declared"),
                                ({"depends_on": ["a"]}, "dependent_capture_groups"),
                                ({"evidence_ref": ""}, "missing_independence_evidence"),
                                ({"expires_at": NOW}, "expired_independence_evidence")]:
            groups = copy.deepcopy(GROUPS)
            groups[1].update(changes)
            with self.subTest(changes=changes):
                result = self.run_estimate(groups=groups)
                self.assertEqual(result["status"], "unknown")
                self.assertIn(reason, result["reasons"])

    def test_small_or_degenerate_overlap_cannot_create_precise_interval(self):
        for data in (records(range(10), range(5, 15)), records(range(100), range(100))):
            result = self.run_estimate(data)
            self.assertEqual(result["status"], "unknown")
            self.assertIn("insufficient_interval_support", result["reasons"])

    def test_synthetic_repeated_sampling_calibrates_only_the_declared_design(self):
        rng = random.Random(731)
        covered = 0
        estimates = []
        for _ in range(120):
            left = rng.sample(range(1000), 400)
            right = rng.sample(range(1000), 500)
            result = self.run_estimate(records(left, right))
            self.assertEqual(result["status"], "estimated")
            estimates.append(result["estimate"])
            covered += result["interval"]["lower"] <= 1000 <= result["interval"]["upper"]
        self.assertLess(abs(sum(estimates) / len(estimates) - 1000), 25)
        self.assertGreaterEqual(covered, 102)  # >=85%, finite synthetic check, not field calibration.

    def test_malformed_structures_dates_and_unbounded_inputs_are_rejected(self):
        for stratum in (STRATUM | {"market_country": ""}, STRATUM | {"vehicle_class": "boat"},
                        STRATUM | {"window_start": "2026-09-01"},
                        STRATUM | {"window_end": "2027-01-01T00:00:00Z"}):
            with self.subTest(stratum=stratum), self.assertRaises(ValueError):
                self.run_estimate(stratum=stratum)
        with self.assertRaises(ValueError):
            self.run_estimate([None])

    def test_same_capture_evidence_cannot_count_as_independent_groups(self):
        data = self.data + [self.data[0] | {"source_id": "b"}]
        result = self.run_estimate(data)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("shared_capture_evidence_between_groups", result["reasons"])

    def test_resource_limits_and_unknown_sources_fail_closed(self):
        from discovery.estimation import MAX_RECORDS
        with self.assertRaises(ValueError):
            self.run_estimate([self.data[0]] * (MAX_RECORDS + 1))
        result = self.run_estimate(self.data + [self.data[0] | {"source_id": "unknown"}])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("source_group_unknown", result["reasons"])
        self.assertEqual(result["excluded_records"], 1)

    def test_records_order_does_not_change_estimate_or_scope(self):
        self.assertEqual(self.run_estimate(), self.run_estimate(list(reversed(self.data))))

    def test_independence_metadata_rejects_arbitrary_secret_fields(self):
        groups = copy.deepcopy(GROUPS)
        groups[0]["api_key"] = "synthetic-marker"
        with self.assertRaises(ValueError):
            self.run_estimate(groups=groups)

    def test_independence_provenance_is_a_detached_public_projection(self):
        groups = copy.deepcopy(GROUPS)
        result = self.run_estimate(groups=groups)
        groups[0]["sources"].append("modified-after-call")
        self.assertEqual(result["independence_evidence"][0]["sources"], ["a"])


if __name__ == "__main__":
    unittest.main()
