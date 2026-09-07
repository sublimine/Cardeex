"""Synthetic catalogue checks; no downloaded national records or external I/O."""

import copy
import hashlib
import importlib.util
import json
import unittest


def payload(units, country="ES", **options):
    body = json.dumps({"units": units}, ensure_ascii=False).encode()
    metadata = {"country": country, "version": "synthetic-2026",
                "source_url": "https://catalogue.example/units.json",
                "observed_at": "2026-09-01T00:00:00Z",
                "body_sha256": hashlib.sha256(body).hexdigest()}
    metadata.update(options)
    return body, metadata


class GeographyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.geography"), "R3 catalogue module absent")
        from discovery import geography
        self.geo = geography

    def load(self, units, country="ES", **metadata):
        return self.geo.load_catalogue(*payload(units, country, **metadata))

    def validator(self):
        self.assertTrue(callable(getattr(self.geo, "validate_catalogue", None)),
                        "serialized catalogue validation is missing")
        return self.geo.validate_catalogue

    def rehash(self, catalogue):
        material = {key: value for key, value in catalogue.items() if key != "normalized_sha256"}
        catalogue["normalized_sha256"] = hashlib.sha256(json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")).hexdigest()
        return catalogue

    def test_serialized_catalogue_roundtrip_has_independently_reproducible_checksum(self):
        catalogue = self.load([{"code": "00001", "name": "Village", "aliases": ["Hamlet"]}])
        self.assertIn("normalized_sha256", catalogue)
        self.assertEqual(catalogue["normalized_sha256"], self.rehash(copy.deepcopy(catalogue))["normalized_sha256"])
        result = self.validator()(json.loads(json.dumps(catalogue)))
        self.assertEqual(result, catalogue)
        self.assertEqual(result["body_sha256"], payload(
            [{"code": "00001", "name": "Village", "aliases": ["Hamlet"]}])[1]["body_sha256"])
        result["units"][0]["aliases"].append("Independent output")
        self.assertEqual(catalogue["units"][0]["aliases"], ["Hamlet"])

    def test_consumers_reject_mutated_metadata_and_units_without_rehash(self):
        original = self.load([{"code": "00001", "name": "Village"}])
        for field, value in (("country", "FR"), ("version", "changed"),
                             ("body_sha256", "0" * 64),
                             ("units", [{"code": "00002", "name": "Changed",
                                         "level": "municipality", "aliases": []}])):
            catalogue = copy.deepcopy(original)
            catalogue[field] = value
            for consumer in (self.geo.catalogue_localities,
                             lambda item: self.geo.resolve_place(item, "Village")):
                with self.subTest(field=field, consumer=consumer), self.assertRaisesRegex(ValueError, "checksum"):
                    consumer(catalogue)

    def test_serialized_top_level_fields_and_schema_are_exact(self):
        validate = self.validator()
        original = self.load([{"code": "00001", "name": "Village"}])
        for changes in ({"unexpected": "value"}, {"catalogue_schema_version": "future"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(self.rehash(original | changes))
        for field in original:
            catalogue = copy.deepcopy(original)
            del catalogue[field]
            if field != "normalized_sha256":
                self.rehash(catalogue)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate(catalogue)

    def test_rehash_does_not_bypass_provenance_or_import_option_validation(self):
        validate = self.validator()
        original = self.load([{"code": "00001", "name": "Village"}])
        for changes in ({"source_url": "https://catalogue.example/data?access_token=synthetic-marker"},
                        {"country": "es"}, {"body_sha256": "INVALID"},
                        {"observed_at": "9999-01-01T00:00:00Z"}, {"format": "yaml"},
                        {"import_options": {"unknown": "value"}},
                        {"import_options": {"delimiter": "!!"}},
                        {"import_options": {"level_map": {"COM": "unsupported"}}},
                        {"import_options": {"field_map": {"code": "COM", "name": "NAME"}}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError) as caught:
                validate(self.rehash(original | changes))
            self.assertNotIn("synthetic-marker", str(caught.exception))

    def test_rehash_does_not_bypass_normalized_unit_or_parent_validation(self):
        validate = self.validator()
        original = self.load([{"code": "00001", "name": "Village"}])
        for changes in ({"code": 1}, {"code": " 00001"}, {"name": "x" * 513},
                        {"rural": 1}, {"parent_code": "missing"},
                        {"parent_code": "00001"}, {"level": "COM"},
                        {"aliases": ["a"] * 33}, {"extra": "value"}):
            catalogue = copy.deepcopy(original)
            catalogue["units"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(self.rehash(catalogue))
        for change in ("missing_defaults", "duplicate_codes"):
            catalogue = copy.deepcopy(original)
            if change == "missing_defaults":
                del catalogue["units"][0]["aliases"]
            else:
                catalogue["units"].append(copy.deepcopy(catalogue["units"][0]))
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(self.rehash(catalogue))

    def test_derived_ambiguities_and_completeness_cannot_be_forged_by_rehash(self):
        validate = self.validator()
        original = self.load([{"code": "00001", "name": "Shared"},
                              {"code": "00002", "name": "Shared"}])
        self.assertEqual(validate(original)["ambiguities"],
                         [{"name_key": "shared", "codes": ["00001", "00002"]}])
        for changes in ({"ambiguities": []},
                        {"completeness": {"status": "complete", "coverage_ratio": 1,
                                          "reason": "claimed_by_input"}}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "canonical|derived"):
                validate(self.rehash(original | changes))

    def test_normalized_integrity_does_not_claim_original_bytes_or_publisher_authentication(self):
        validate = self.validator()
        catalogue = self.load([{"code": "00001", "name": "Village"}])
        catalogue.update(version="another-declared-version", body_sha256="0" * 64)
        result = validate(self.rehash(catalogue))
        self.assertEqual(result["body_sha256"], "0" * 64)
        self.assertEqual(result["completeness"]["status"], "unknown")
        self.assertIsNone(result["completeness"]["coverage_ratio"])

    def test_serialized_input_has_finite_depth_node_and_byte_limits(self):
        validate = self.validator()
        original = self.load([])
        for scalar in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(scalar=scalar), self.assertRaisesRegex(ValueError, "finite"):
                validate(original | {"unexpected": scalar})
        nested = []
        for _ in range(self.geo.MAX_JSON_DEPTH + 1):
            nested = [nested]
        with self.assertRaisesRegex(ValueError, "depth"):
            validate(original | {"unexpected": nested})
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, "cyclic|depth"):
            validate(original | {"unexpected": cyclic})
        with self.assertRaisesRegex(ValueError, "node"):
            validate(original | {"units": [None] * (self.geo.MAX_JSON_NODES + 1)})
        with self.assertRaisesRegex(ValueError, "bytes"):
            validate(original | {"units": ["x" * 4096] * (self.geo.MAX_SERIALIZED_BYTES // 4096 + 1)})

    def test_original_json_rejects_exponent_overflow_and_bounded_nested_payloads(self):
        for body, reason in ((b'{"units":[{"code":"1","name":1e999}]}', "finite"),
                             (b'{"units":' + b'[' * 30 + b']' * 30 + b'}', "depth")):
            _, metadata = payload([])
            metadata["body_sha256"] = hashlib.sha256(body).hexdigest()
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.geo.load_catalogue(body, metadata)

    def test_codes_are_preserved_for_each_country_and_not_numerically_coerced(self):
        for country, code in [("ES", "01001"), ("FR", "2A001"), ("DE", "01001000"),
                              ("NL", "GM0001"), ("BE", "01001"), ("CH", "0001")]:
            with self.subTest(country=country):
                catalogue = self.load([{"code": code, "name": "Synthetic"}], country)
                self.assertEqual(self.geo.catalogue_localities(catalogue),
                                 [{"country": country, "code": code, "name": "Synthetic"}])
        with self.assertRaises(ValueError):
            self.load([{"code": 1001, "name": "Synthetic"}])

    def test_resolution_is_exact_multilingual_and_preserves_ambiguity(self):
        catalogue = self.load([
            {"code": "00001", "name": "Sant Martí", "aliases": ["San Martín"], "region": "North"},
            {"code": "00002", "name": "San Martín", "region": "South"}])
        result = self.geo.resolve_place(catalogue, "san martín")
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["code"])
        self.assertEqual(result["candidates"], ["00001", "00002"])
        result = self.geo.resolve_place(catalogue, "San Martín", "north")
        self.assertEqual((result["status"], result["code"]), ("matched", "00001"))
        self.assertEqual(result["method"], "exact_alias")
        self.assertEqual(result["provenance"]["version"], "synthetic-2026")
        self.assertIn("normalized_sha256", result["provenance"])
        self.assertEqual(self.geo.resolve_place(catalogue, "San Martin")["status"], "unknown")

    def test_resolution_retains_exact_original_query_and_normalization_rule(self):
        catalogue = self.load([{"code": "1", "name": "San Martín", "region": "North"}])
        result = self.geo.resolve_place(catalogue, " san martín ", "  NORTH  ")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["query_original"], {"name": " san martín ", "region": "  NORTH  "})
        self.assertEqual(result["query_normalized"], {"name": "san martín", "region": "north"})
        self.assertEqual(result["normalization_rule"], "unicode_nfc_casefold_collapse_whitespace_v1")
        unknown = self.geo.resolve_place(catalogue, "Unseen")
        self.assertEqual(unknown["query_original"], {"name": "Unseen", "region": None})

    def test_nuclei_keep_their_codes_and_parents(self):
        catalogue = self.load([
            {"code": "01001", "name": "Parent", "level": "municipality"},
            {"code": "01001000101", "name": "Small Hamlet", "parent_code": "01001",
             "level": "settlement", "rural": True}])
        self.assertEqual(len(self.geo.catalogue_localities(catalogue)), 2)
        self.assertEqual(catalogue["units"][1]["parent_code"], "01001")
        self.assertEqual(self.geo.resolve_place(catalogue, "Small Hamlet")["code"], "01001000101")

    def test_parent_regions_do_not_become_planner_localities(self):
        catalogue = self.load([{"code": "R1", "name": "Region", "level": "region"},
                               {"code": "00001", "name": "Village", "parent_code": "R1"}])
        self.assertEqual([r["code"] for r in self.geo.catalogue_localities(catalogue)], ["00001"])

    def test_checksum_must_match_original_bytes(self):
        body, metadata = payload([{"code": "00001", "name": "Village"}])
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.geo.load_catalogue(body + b" ", metadata)

    def test_country_and_provenance_are_required(self):
        body, metadata = payload([{"code": "00001", "name": "Village"}])
        for field in ("country", "version", "source_url", "observed_at", "body_sha256"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.geo.load_catalogue(body, {k: v for k, v in metadata.items() if k != field})
        for value in ("2026-02-30T00:00:00Z", "2026-09-01", "9999-01-01T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.geo.load_catalogue(body, metadata | {"observed_at": value})

    def test_provenance_requires_safe_https_without_credentials_or_secret_query(self):
        for url in ("https://catalogue.example/data?access_token=synthetic-marker",
                    "https://user:synthetic-marker@catalogue.example/data",
                    "http://catalogue.example/data", "https://127.0.0.1/data"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.load([{"code": "1", "name": "A"}], source_url=url)
        catalogue = self.load([{"code": "1", "name": "A"}],
                              source_url="https://CATALOGUE.EXAMPLE:443/data")
        self.assertEqual(catalogue["source_url"], "https://catalogue.example/data")

    def test_duplicate_codes_missing_parents_and_cycles_are_rejected(self):
        cases = [[{"code": "1", "name": "A"}, {"code": "1", "name": "B"}],
                 [{"code": "1", "name": "A", "parent_code": "missing"}],
                 [{"code": "1", "name": "A", "parent_code": "2"},
                  {"code": "2", "name": "B", "parent_code": "1"}]]
        for units in cases:
            with self.subTest(units=units), self.assertRaises(ValueError):
                self.load(units)

    def test_hostile_json_duplicate_keys_and_non_finite_values_are_rejected(self):
        for body in (b'{"units":[],"units":[]}', b'{"units":[{"code":"1","name":NaN}]}',
                     b'[' * 2000 + b']' * 2000):
            _, metadata = payload([])
            metadata["body_sha256"] = hashlib.sha256(body).hexdigest()
            with self.subTest(body=body[:50]), self.assertRaises(ValueError):
                self.geo.load_catalogue(body, metadata)

    def test_csv_field_mapping_preserves_codes_and_official_parent_fields(self):
        body = ("COM,LIBELLE,REG,COMPARENT,TYPECOM,UNUSED\n"
                "01001,Parent,01,,COM,unused\n"
                "01002,Child,01,01001,COMD,unused\n").encode()
        _, metadata = payload([], "FR")
        metadata.update(body_sha256=hashlib.sha256(body).hexdigest(),
                        field_map={"code": "COM", "name": "LIBELLE", "region": "REG",
                                   "parent_code": "COMPARENT", "level": "TYPECOM"},
                        level_map={"COM": "municipality", "COMD": "settlement"})
        result = self.geo.load_catalogue(body, metadata, format="csv")
        self.assertEqual(result["units"][0]["code"], "01001")
        self.assertEqual(result["units"][1]["parent_code"], "01001")
        self.assertEqual(result["units"][1]["level"], "settlement")

    def test_csv_rejects_duplicate_headers_missing_cells_and_bad_booleans(self):
        for body in (b"code,name,name\n01,A,B\n", b"code,name\n01\n",
                     b"code,name,rural\n01,A,yes\n"):
            _, metadata = payload([])
            metadata["body_sha256"] = hashlib.sha256(body).hexdigest()
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.geo.load_catalogue(body, metadata, format="csv")

    def test_units_and_aliases_are_bounded_and_types_strict(self):
        for changes in ({"aliases": ["a"] * 33}, {"rural": 1}, {"name": "x" * 513},
                        {"parent_code": 1}, {"code": "a\n"}, {"level": "invented"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load([{"code": "1", "name": "Village"} | changes])

    def test_catalogue_never_self_certifies_and_order_is_deterministic(self):
        units = [{"code": "2", "name": "B"}, {"code": "1", "name": "A"}]
        catalogue = self.load(units)
        self.assertEqual(catalogue["completeness"]["status"], "unknown")
        self.assertIsNone(catalogue["completeness"]["coverage_ratio"])
        self.assertEqual(self.geo.catalogue_localities(catalogue),
                         self.geo.catalogue_localities(self.load(list(reversed(units)))))

    def test_malformed_mapping_values_raise_contract_errors(self):
        for options in ({"field_map": {"code": [], "name": "name"}},
                        {"level_map": {"COM": []}}, {"delimiter": []}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.load([{"code": "1", "name": "A"}], **options)

    def test_limits_and_empty_catalogues_do_not_turn_into_complete_geography(self):
        body, metadata = payload([])
        self.assertEqual(self.geo.catalogue_localities(self.geo.load_catalogue(body, metadata)), [])
        oversized = b" " * (self.geo.MAX_BODY_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "bounded bytes"):
            self.geo.load_catalogue(oversized, metadata)
        units = [{"code": str(n), "name": str(n), "parent_code": str(n + 1)} for n in range(65)]
        units.append({"code": "65", "name": "Last"})
        with self.assertRaisesRegex(ValueError, "depth"):
            self.load(units)


if __name__ == "__main__":
    unittest.main()
