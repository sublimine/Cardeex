"""Country configuration is an evidence-backed, strict input boundary."""

import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest


class ProfileTests(unittest.TestCase):
    def api(self):
        try:
            return importlib.import_module("discovery.profiles")
        except ModuleNotFoundError:
            self.fail("The country profile loader has not been implemented")

    def test_six_bespoke_profiles_cover_four_classes_and_all_families(self):
        profiles = self.api().load_profiles()
        self.assertEqual(set(profiles), {"ES", "FR", "DE", "NL", "BE", "CH"})
        families = set()
        for country, profile in profiles.items():
            with self.subTest(country=country):
                self.assertEqual(profile["country"], country)
                self.assertEqual(set(profile["vehicle_classes"]), {"car", "lcv", "motorcycle", "motorhome"})
                self.assertGreaterEqual(len(profile["sources"]), 4)
                self.assertGreaterEqual(len({s["family"] for s in profile["strategies"]}), 6)
                for strategy in profile["strategies"]:
                    families.add(strategy["family"])
                    self.assertTrue(strategy["source_ids"])
        self.assertEqual(families, {"official_registry", "geo_directory", "marketplace", "manufacturer_network", "trade_directory", "web_index", "local_search", "social_public"})

    def test_multilingual_regions_are_first_class(self):
        profiles = self.api().load_profiles()
        for country, languages in {"ES": {"es", "ca", "eu", "gl"}, "BE": {"nl", "fr", "de"}, "CH": {"de", "fr", "it", "rm"}, "NL": {"nl", "fy"}}.items():
            self.assertTrue(languages <= set(profiles[country]["languages"]))
            self.assertTrue(languages <= set(profiles[country]["vocabulary"]))

    def test_profile_loads_are_independent_and_country_is_case_normalized(self):
        api = self.api()
        profile = api.load_profile("es")
        profile["languages"].clear()
        self.assertIn("es", api.load_profile("ES")["languages"])

    def test_extension_uses_profile_directory_without_changing_loader(self):
        api = self.api()
        profile = api.load_profile("ES")
        profile["country"] = "PT"
        profile["name"] = "Portugal test configuration"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "PT.json").write_text(json.dumps(profile), encoding="utf-8")
            self.assertEqual(set(api.load_profiles(directory)), {"PT"})
            self.assertEqual(api.load_profile("PT", directory)["country"], "PT")

    def test_invalid_input_is_rejected_with_value_error(self):
        api = self.api()
        for country in ("../ES", "E", "ESP", " ES", "1A", "", None, True):
            with self.subTest(country=country), self.assertRaises(ValueError):
                api.load_profile(country)
        with self.assertRaises(ValueError):
            api.load_profile("ZZ")

    def test_malformed_or_ambiguous_files_are_rejected(self):
        api = self.api()
        profile = api.load_profile("ES")
        cases = ["{", '{"country":"ES","country":"FR"}', json.dumps({**profile, "country": "FR"})]
        for payload in cases:
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "ES.json").write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload[:40]), self.assertRaises(ValueError):
                    api.load_profile("ES", directory)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                api.load_profiles(directory)

    def test_contract_rejects_typos_missing_classes_and_invalid_nested_values(self):
        api = self.api()
        original = api.load_profile("ES")
        mutations = [
            lambda p: p.update(unknown_flag=True),
            lambda p: p.update(schema_version="2.0.0"),
            lambda p: p.update(review_days=True),
            lambda p: p.update(review_days=0),
            lambda p: p.update(vehicle_classes=["car"]),
            lambda p: p.update(languages=["es", "es"]),
            lambda p: p.update(blind_spots=[]),
            lambda p: p["geography"].update(catalogue_url="http://example.org"),
            lambda p: p["geography"].update(unknown="field"),
            lambda p: p["sources"][0].update(checked_at="2026-02-30"),
            lambda p: p["sources"][0].update(checked_at="20260907"),
            lambda p: p["sources"][0].update(url="https://user:password@example.org/"),
            lambda p: p["sources"][0].update(url="https://example.org:invalid/"),
            lambda p: p["sources"].append(copy.deepcopy(p["sources"][0])),
            lambda p: p["strategies"].append(copy.deepcopy(p["strategies"][0])),
            lambda p: p["strategies"][0].update(priority=True),
            lambda p: p["strategies"][0].update(priority=101),
            lambda p: p["strategies"][0].update(access_mode="approved"),
            lambda p: p["strategies"][0].update(source_type="directory"),
            lambda p: p["strategies"][0].update(source_ids=["nonexistent"]),
            lambda p: p["strategies"][0].update(vehicle_classes=["truck"]),
            lambda p: p["strategies"][0].update(query_templates={"xx": ["{locality}"]}),
            lambda p: p["strategies"][0].update(query_templates={"es": ["dealers"]}),
            lambda p: p["strategies"][0].update(query_templates={"es": ["{locality.__class__}"]}),
            lambda p: p["strategies"][0].update(query_templates={"es": ["{locality!r}"]}),
            lambda p: p["strategies"][0].update(query_templates={"es": ["{locality:>100}"]}),
            lambda p: p["vocabulary"]["es"].update(car=[]),
            lambda p: p["vocabulary"].pop("es"),
        ]
        for mutation in mutations:
            profile = copy.deepcopy(original)
            mutation(profile)
            with self.subTest(mutation=mutation.__code__.co_firstlineno), self.assertRaises(ValueError):
                api.validate_profile(profile)

    def test_templates_render_for_every_supported_language_and_vehicle_class(self):
        for profile in self.api().load_profiles().values():
            for strategy in profile["strategies"]:
                for language, templates in strategy["query_templates"].items():
                    for vehicle_class in strategy["vehicle_classes"]:
                        for template in templates:
                            rendered = template.format(locality="Tiny rural locality", vehicle_term=profile["vocabulary"][language][vehicle_class][0])
                            self.assertIn("Tiny rural locality", rendered)

    def test_every_config_url_reuses_the_offline_transport_boundary(self):
        api = self.api()
        original = api.load_profile("ES")
        unsafe = ["https://127.0.0.1/", "https://[::1]/", "https://localhost/",
                  "https://service.internal/", "https://example.org:444/",
                  "https://example.org/?access_token=sample",
                  "https://example.org/#/inventory?access_token=sample"]
        for url in unsafe:
            for location in ("seed", "source", "geography"):
                profile = copy.deepcopy(original)
                if location == "seed":
                    profile["strategies"][0]["seed_urls"][0] = url
                elif location == "source":
                    profile["sources"][-1]["url"] = url
                else:
                    profile["geography"]["catalogue_url"] = url
                    profile["sources"][0]["url"] = url
                with self.subTest(url=url, location=location), self.assertRaises(ValueError):
                    api.validate_profile(profile)

    def test_extension_profile_file_is_read_with_a_finite_byte_limit(self):
        api = self.api()
        payload = json.dumps(api.load_profile("ES")) + " " * (1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "ES.json").write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "byte limit"):
                api.load_profile("ES", directory)

    def test_profile_text_and_collections_have_finite_limits(self):
        api = self.api()
        original = api.load_profile("ES")
        mutations = [lambda p: p.update(name="x" * 8193),
                     lambda p: p.update(blind_spots=[f"gap-{n}" for n in range(4097)]),
                     lambda p: p["strategies"].extend([p["strategies"][0]] * 4097),
                     lambda p: p["sources"].extend([p["sources"][0]] * 4097)]
        for mutation in mutations:
            profile = copy.deepcopy(original)
            mutation(profile)
            with self.subTest(mutation=mutation.__code__.co_firstlineno), self.assertRaisesRegex(ValueError, "limit"):
                api.validate_profile(profile)

    def test_deep_json_is_a_value_error_not_an_uncaught_recursion_error(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "ES.json").write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
            with self.assertRaises(ValueError):
                api.load_profile("ES", directory)

    def test_in_memory_profiles_have_total_text_and_node_limits(self):
        api = self.api()
        profile = api.load_profile("ES")
        profile["blind_spots"] = [str(n) + "x" * 8188 for n in range(129)]
        with self.assertRaisesRegex(ValueError, "total text limit"):
            api.validate_profile(profile)
        profile = api.load_profile("ES")
        for language in ("es", "ca"):
            for kind in profile["vehicle_classes"]:
                profile["vocabulary"][language][kind] = [f"term-{n}" for n in range(3000)]
        with self.assertRaisesRegex(ValueError, "node limit"):
            api.validate_profile(profile)

    def test_extension_directory_has_a_finite_country_file_limit(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            for n in range(257):
                country = chr(65 + n // 26) + chr(65 + n % 26)
                Path(directory, country + ".json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "file limit"):
                api.load_profiles(directory)

    def test_kvk_citation_records_suspension_without_approving_access(self):
        profile = self.api().load_profile("NL")
        source = next(row for row in profile["sources"] if row["id"] == "kvk_terms")
        self.assertIn("suspend", source["access_notes"].lower())
        self.assertIn("5.2", source["access_notes"])
        self.assertIn("5.4", source["access_notes"])
        self.assertIn("5.6", source["access_notes"])
        strategy = next(row for row in profile["strategies"] if row["id"] == "nl_kvk_restricted")
        self.assertEqual(strategy["access_mode"], "permission_required")
        self.assertIn("independent review", strategy["notes"].lower())


if __name__ == "__main__":
    unittest.main()
