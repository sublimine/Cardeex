"""Synthetic source-format fixtures; no service, account or campaign is used."""
import importlib
import json
import unittest
from urllib.parse import parse_qs, urlsplit


class ChannelTests(unittest.TestCase):
    def setUp(self):
        try:
            self.c = importlib.import_module("discovery.channels")
        except ModuleNotFoundError:
            self.fail("Source-specific discovery channels are not implemented")

    def binding(self, adapter="searxng_json", **parameters):
        endpoints = {
            "searxng_json": "https://search.example/search",
            "overpass_json": "https://overpass.example/api/interpreter",
            "rdw_socrata": "https://opendata.rdw.nl/resource/5k74-3jha.json",
            "sirene_csv": "https://data.example/stock.csv",
        }
        return dict(adapter=adapter, endpoint=endpoints[adapter], assessment_ref="assessment:fixture",
                    countries=["FR", "NL"], vehicle_classes=["car", "motorcycle"],
                    parameters=parameters, max_pages=3)

    def parse(self, binding, payload, origin=None):
        return self.c.parse_response(binding, json.dumps(payload).encode(), "application/json",
                                     origin or binding["endpoint"])

    def test_binding_is_explicit_strict_and_normalized_without_mutating_input(self):
        original = self.binding()
        normalized = self.c.validate_binding(original)
        self.assertEqual(normalized["countries"], ["FR", "NL"])
        self.assertEqual(original["parameters"], {})
        self.assertEqual(normalized, self.c.validate_binding(normalized))
        for bad in (None, {}, {**original, "adapter": "nominal"}, {**original, "max_pages": True},
                    {**original, "countries": []}, {**original, "vehicle_classes": ["truck"]},
                    {**original, "parameters": {"api_key": "secret"}}, {**original, "extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.c.validate_binding(bad)

    def test_endpoint_rejects_credentials_http_query_and_private_hosts(self):
        for endpoint in ("http://search.example/search", "https://127.0.0.1/", "https://user@search.example/",
                         "https://search.example/?q=hello", "https://search.example/#x"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.c.validate_binding({**self.binding(), "endpoint": endpoint})

    def test_search_request_encodes_query_and_pagination(self):
        b = self.binding(language="fr-FR")
        url = self.c.request_url(b, {"query": 'garage "petit" & moto'}, "2")
        self.assertEqual(parse_qs(urlsplit(url).query), {
            "q": ['garage "petit" & moto'], "format": ["json"], "pageno": ["2"], "language": ["fr-FR"]})
        with self.assertRaises(ValueError):
            self.c.request_url(b, {"query": "garage"}, "https://evil.example/")
        with self.assertRaises(ValueError):
            self.c.request_url(b, {})

    def test_request_parameters_cannot_falsely_complete_source_slice(self):
        b = self.binding("overpass_json", bbox=[40, 0, 41, 1], row_limit=500)
        different = {**b, "parameters": {**b["parameters"], "row_limit": 1}}
        origin = self.c.request_url(different, {})
        with self.assertRaises(ValueError):
            self.parse(b, {"elements": [{"type": "node", "id": 1}]}, origin)
        unknown = self.parse(b, {"elements": []})
        self.assertIsNone(unknown["complete"])
        rdw = self.parse(self.binding("rdw_socrata", page_size=2000), [])
        self.assertIsNone(rdw["complete"])
        self.assertIn("request_parameters_unverified", rdw["warnings"])

    def test_search_cursor_exhaustion_is_explicit_not_unusable_continuation(self):
        b = self.binding()
        origin = self.c.request_url(b, {"query": "garage"}, "999999999999999999")
        page = self.parse(b, {"results": [{"url": "https://dealer.example/"}]}, origin)
        self.assertIsNone(page["continuation"])
        self.assertIn("cursor_range_exhausted", page["warnings"])

    def test_search_claims_do_not_inherit_query_country_class_or_rank_as_confidence(self):
        b = self.binding()
        page = self.parse(b, {"query": "garage FR", "results": [
            {"url": "https://small.example/", "title": "Village garage", "score": 99,
             "content": "private unnecessary snippet", "engine": "demo"}], "unresponsive_engines": []},
            "https://search.example/search?q=garage&pageno=1")
        row = page["rows"][0]
        self.assertEqual(row["kind"], "unknown")
        self.assertEqual(row["locator"], "https://small.example/")
        self.assertEqual(page["continuation"], "2")
        self.assertIsNone(page["complete"])
        for key in ("claimed_country", "claimed_vehicle_classes", "market_country", "confidence", "score", "content"):
            self.assertNotIn(key, row["signals"])
        self.assertNotIn("private unnecessary", json.dumps(page))

    def test_search_empty_and_last_budget_page_keep_uncertainty(self):
        b = self.binding()
        empty = self.parse(b, {"results": []})
        self.assertEqual(empty["rows"], [])
        self.assertIsNone(empty["complete"])
        self.assertIsNone(empty["continuation"])
        last = self.parse(b, {"results": [{"url": "https://x.example/"}]},
                          "https://search.example/search?pageno=3")
        self.assertEqual(last["continuation"], "4")  # Engine persists debt beyond the run budget.
        self.assertIn("page_budget_reached", last["warnings"])

    def test_search_source_failures_and_invalid_urls_are_explicit(self):
        page = self.parse(self.binding(), {"results": [{"url": "https://127.0.0.1/"},
            {"url": "https://good.example/"}], "unresponsive_engines": [["demo", "timeout"]]})
        self.assertEqual(len(page["rows"]), 1)
        self.assertIn("upstream_search_errors", page["warnings"])
        self.assertIn("unsafe_or_invalid_locator", page["warnings"])
        for payload in ({"error": "unavailable"}, {"results": {}}, {"results": ["wrong"]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.parse(self.binding(), payload)

    def test_overpass_request_requires_bounded_geography_and_valid_tag_filter(self):
        b = self.binding("overpass_json", bbox=[48.0, 2.0, 48.1, 2.1], tags=[{"key": "shop", "value": "car"}], row_limit=5)
        query = parse_qs(urlsplit(self.c.request_url(b, {})).query)["data"][0]
        self.assertIn('nwr["shop"="car"](48', query)
        self.assertIn("out center 5;", query)
        with self.assertRaises(ValueError):
            self.c.request_url(self.binding("overpass_json"), {})
        with self.assertRaises(ValueError):
            self.c.validate_binding(self.binding("overpass_json", bbox=[0, 0, 90, 180]))
        with self.assertRaises(ValueError):
            self.c.validate_binding(self.binding("overpass_json", tags=[{"key": "shop", "value": 'car"];out;'}]))

    def test_overpass_cannot_override_binding_and_huge_coordinates_are_safe_errors(self):
        b = self.binding("overpass_json", bbox=[40, 0, 41, 1])
        with self.assertRaises(self.c.ChannelError):
            self.c.request_url(b, {"bbox": [41, 0, 42, 1]})
        with self.assertRaises(self.c.ChannelError):
            self.c.request_url(self.binding("overpass_json"), {"bbox": [40, 0, 41, 1]})
        with self.assertRaises(self.c.ChannelError):
            self.c.validate_binding(self.binding("overpass_json", bbox=[10**309, 0, 41, 1]))
        with self.assertRaises(self.c.ChannelError):
            self.parse(b, {"elements": [{"type": "node", "id": 1, "lat": 10**309, "lon": 0}]}, self.c.request_url(b, {}))

    def test_overpass_unnamed_without_website_retains_record_and_way_center(self):
        b = self.binding("overpass_json", bbox=[48, 2, 49, 3], row_limit=5)
        page = self.parse(b, {"version": 0.6, "osm3s": {"timestamp_osm_base": "2026-09-01T00:00:00Z"},
            "elements": [{"type": "way", "id": 123, "center": {"lat": 48.1, "lon": 2.1},
                          "tags": {"shop": "car", "addr:country": "FR", "phone": "private"}}]}, self.c.request_url(b, {}))
        row = page["rows"][0]
        self.assertEqual(row["locator"], "https://www.openstreetmap.org/way/123")
        self.assertEqual(row["signals"]["locator_role"], "registry_record")
        self.assertEqual(row["signals"]["published_coordinates"]["precision"], "bounding_box_center")
        self.assertEqual(row["signals"]["source_timestamp"], "2026-09-01T00:00:00Z")
        self.assertNotIn("private", json.dumps(page))
        self.assertTrue(page["complete"])

    def test_overpass_website_is_claim_not_identity_and_parts_are_not_sales(self):
        page = self.parse(self.binding("overpass_json"), {"elements": [
            {"type": "node", "id": 1, "lat": 0, "lon": 0, "tags": {
                "shop": "car_parts", "website": "http://parts.example/", "name": "Parts"}}]})
        self.assertEqual(page["rows"][0]["kind"], "unknown")
        self.assertEqual(page["rows"][0]["locator"], "http://parts.example/")
        self.assertNotIn("claimed_vehicle_classes", page["rows"][0]["signals"])

    def test_overpass_remark_and_limit_never_report_completion(self):
        b = self.binding("overpass_json", row_limit=1)
        page = self.parse(b, {"elements": [{"type": "node", "id": 1, "tags": {"shop": "car"}}],
                              "remark": "runtime error: timeout"})
        self.assertFalse(page["complete"])
        self.assertIn("overpass_remark", page["warnings"])
        self.assertIn("row_limit_reached", page["warnings"])

    def test_overpass_malformed_ids_and_timestamps_fail_explicitly(self):
        for payload in ({"elements": [{"type": "node", "id": True, "tags": {}}]},
                        {"elements": [], "osm3s": {"timestamp_osm_base": "yesterday"}},
                        {"elements": "not-a-list"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.parse(self.binding("overpass_json"), payload)

    def test_rdw_keyset_request_and_directory_record_without_web(self):
        b = self.binding("rdw_socrata", page_size=2)
        query = parse_qs(urlsplit(self.c.request_url(b, {}, "9")).query)
        self.assertEqual(query["$where"], ["volgnummer > 9"])
        self.assertEqual(query["$order"], ["volgnummer ASC"])
        self.assertEqual(query["$limit"], ["2"])
        page = self.parse(b, [{"volgnummer": "10", "naam_bedrijf": "Garage Example", "plaats": "Dorp",
                              "straat": "Teststraat", "huisnummer": "2", "postcode_numeriek": "1234",
                              "postcode_alfanumeriek": "AB", "phone": "private"},
                             {"volgnummer": "11", "naam_bedrijf": "Repair Example"}], self.c.request_url(b, {}, "9"))
        self.assertEqual(page["continuation"], "11")
        self.assertFalse(page["complete"])
        row = page["rows"][0]
        self.assertEqual(row["kind"], "unknown")
        self.assertTrue(row["locator"].endswith("#rdw=10"))
        self.assertEqual(row["signals"]["source_record_id"], "10")
        self.assertNotIn("private", json.dumps(page))

    def test_rdw_empty_short_unsorted_duplicate_and_repeated_cursor(self):
        b = self.binding("rdw_socrata", page_size=2)
        self.assertTrue(self.parse(b, [], self.c.request_url(b, {}))["complete"])
        self.assertTrue(self.parse(b, [{"volgnummer": "2", "naam_bedrijf": "Small"}], self.c.request_url(b, {}))["complete"])
        for rows in ([{"volgnummer": "2"}, {"volgnummer": "1"}], [{"volgnummer": "2"}, {"volgnummer": "2"}],
                     [{"unexpected": 1}], [{"volgnummer": "1.5"}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.parse(b, rows)
        with self.assertRaises(ValueError):
            self.parse(b, [{"volgnummer": "2", "naam_bedrijf": "Repeat"}],
                       self.c.request_url(b, {}, "2"))

    def test_rdw_rejects_other_datasets_or_credential_parameters(self):
        with self.assertRaises(ValueError):
            self.c.validate_binding({**self.binding("rdw_socrata"), "endpoint": "https://data.example/resource/other.json"})
        with self.assertRaises(ValueError):
            self.c.request_url(self.binding("rdw_socrata"), {}, "0 OR 1=1")

    def sirene(self, rows, header=None):
        import csv
        import io
        columns = header or ["siret", "statutDiffusionEtablissement", "etatAdministratifEtablissement",
                            "activitePrincipaleEtablissement", "nomenclatureActivitePrincipaleEtablissement",
                            "denominationUsuelleEtablissement", "codeCommuneEtablissement", "libelleCommuneEtablissement",
                            "dateDernierTraitementEtablissement", "nomUniteLegale"]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        return output.getvalue().encode()

    def test_sirene_local_records_keep_identifier_closed_status_and_privacy(self):
        b = self.binding("sirene_csv", snapshot_date="2026-08-31", activity_codes=["45.11Z"], nomenclature="NAFRev2")
        body = self.sirene([["12345678900011", "O", "F", "45.11Z", "NAFRev2", "Petit garage", "01001", "Village",
                            "2026-08-30T10:00:00", "private natural person"]])
        page = self.c.parse_response(b, body, "text/csv", b["endpoint"])
        row = page["rows"][0]
        self.assertTrue(row["locator"].endswith("#siret=12345678900011"))
        self.assertEqual(row["kind"], "unknown")
        self.assertEqual(row["signals"]["observed_status"], "F")
        self.assertEqual(row["signals"]["source_timestamp"], "2026-08-30T10:00:00")
        self.assertEqual(row["signals"]["source_snapshot_date"], "2026-08-31")
        self.assertNotIn("private natural person", json.dumps(page))
        self.assertTrue(page["complete"])
        with self.assertRaises(ValueError):
            self.c.request_url(b, {})

    def test_sirene_partial_diffusion_is_not_imported_or_silently_counted_as_complete(self):
        b = self.binding("sirene_csv", snapshot_date="2026-08-31", activity_codes=["45.11Z"], nomenclature="NAFRev2")
        body = self.sirene([["12345678900011", "P", "A", "45.11Z", "NAFRev2", "private", "01001", "Village", "", "private"]])
        page = self.c.parse_response(b, body, "text/csv", b["endpoint"])
        self.assertEqual(page["rows"], [])
        self.assertIn("restricted_diffusion_omitted", page["warnings"])
        self.assertNotIn("private", json.dumps(page))

    def test_sirene_nomenclature_drift_and_bad_csv_fail(self):
        b = self.binding("sirene_csv", snapshot_date="2026-08-31", activity_codes=["45.11Z"], nomenclature="NAFRev2")
        bad = self.sirene([["12345678900011", "O", "A", "45.11Z", "NAF2025", "X", "", "", "", ""]])
        for body in (bad, b'siret,siret\n1,2\n', b'siret,statutDiffusionEtablissement\n"unterminated'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.c.parse_response(b, body, "text/csv", b["endpoint"])

    def test_json_duplicate_keys_nonfinite_wrong_mime_bytes_and_deep_input_fail(self):
        b = self.binding()
        for body, mime in ((b'{"results":[],"results":[]}', "application/json"),
                           (b'{"results":[],"n":NaN}', "application/json"),
                           (b'{"results":[]}', "text/html"), (b"", "application/json"),
                           (("[" * 40 + "0" + "]" * 40).encode(), "application/json"),
                           (b"x" * (2 * 1024 * 1024 + 1), "application/json")):
            with self.subTest(mime=mime, length=len(body)), self.assertRaises(ValueError):
                self.c.parse_response(b, body, mime, b["endpoint"])

    def test_origin_cannot_switch_provider(self):
        with self.assertRaises(ValueError):
            self.parse(self.binding(), {"results": []}, "https://evil.example/search")

    def test_rdw_request_parameter_drift_cannot_certify_a_short_page(self):
        b = self.binding("rdw_socrata", page_size=2)
        for suffix in ("?$limit=1", "?$order=naam_bedrijf", "?$offset=500", "?$where=volgnummer%20%3E%200&$limit=1"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.parse(b, [{"volgnummer": "1", "naam_bedrijf": "Garage"}], b["endpoint"] + suffix)

    def test_invalid_source_metadata_cannot_masquerade_as_success(self):
        for payload in ({"elements": [], "error": "provider error"}, {"elements": [], "remark": {}},
                        {"elements": [], "version": "wrong"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.parse(self.binding("overpass_json"), payload)
        with self.assertRaises(ValueError):
            self.parse(self.binding(), {"results": [], "unresponsive_engines": "wrong-format"})

    def test_record_locators_never_enable_followup_fetch(self):
        b = self.binding("rdw_socrata")
        page = self.parse(b, [{"volgnummer": "1", "naam_bedrijf": "Garage"}])
        self.assertFalse(page["rows"][0]["signals"]["discovery_fetch_allowed"])
        b = self.binding("overpass_json")
        page = self.parse(b, {"elements": [{"type": "node", "id": 1, "tags": {"shop": "car"}}]})
        self.assertFalse(page["rows"][0]["signals"]["discovery_fetch_allowed"])


if __name__ == "__main__":
    unittest.main()
