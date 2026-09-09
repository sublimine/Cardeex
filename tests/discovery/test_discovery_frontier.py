"""Synthetic counterexamples from the discovery capability audit."""
import json
import unittest

from discovery.adapters import parse_document
from discovery.engine import expansion_tasks, observations_from_document


def task(locator="https://garage.example/sitemap.xml"):
    return dict(task_key="audit", plan_id="audit", stratum="ES:one", country="ES",
                classes=["car"], strategy="audit", kind="fetch", locator=locator,
                priority=10, access_mode="public_web", payload={"depth": 0})


class DiscoveryFrontierTests(unittest.TestCase):
    def test_observed_inventory_surface_stays_review_debt_even_with_duplicate_generic_link(self):
        body = b'<a href="/inventory">Inventory</a><iframe src="/inventory"></iframe>'
        rows, _, _ = observations_from_document(body, "text/html", "https://garage.example/",
            policy_ref="audit:fixture", evidence_group="web:garage", observed_at="2026-09-01T00:00:00Z",
            expires_at="2026-10-01T00:00:00Z")
        self.assertTrue(any(row["signals"].get("requires_access_review") for row in rows))
        self.assertFalse(any(child["locator"].endswith("/inventory") for child in expansion_tasks(task("https://garage.example/"), rows)))

    def test_osm_business_without_website_survives_with_registry_identity(self):
        body = json.dumps({"elements": [
            {"type": "node", "id": 12, "lat": 0, "lon": 0,
             "tags": {"shop": "car", "name": "Synthetic garage", "addr:city": "Example"}},
            {"type": "way", "id": 12, "center": {"lat": 47, "lon": 8},
             "tags": {"shop": "car", "name": "Another garage"}},
        ]}).encode()
        rows, observations, _ = observations_from_document(
            body, "application/json", "https://www.openstreetmap.org/",
            policy_ref="audit:fixture", evidence_group="osm", observed_at="2026-09-01T00:00:00Z",
            expires_at="2026-10-01T00:00:00Z")
        sellers = [r for r in rows if r["kind"] == "professional_seller"]
        self.assertEqual({r["locator"] for r in sellers}, {
            "https://www.openstreetmap.org/node/12", "https://www.openstreetmap.org/way/12"})
        self.assertEqual(len({o.candidate_key for o in observations if o.kind == "professional_seller"}), 2)
        self.assertTrue(all(r["signals"]["website_status"] == "not_published" for r in sellers))
        self.assertEqual(len([r for r in rows if r["kind"] == "point_of_sale"]), 2)
        self.assertEqual(expansion_tasks(task(), rows), [])

    def test_invalid_osm_website_preserves_record_without_exposing_secret(self):
        body = json.dumps({"elements": [{"type": "node", "id": 4,
            "tags": {"shop": "car", "name": "Fixture", "website": "https://site.example/?token=secret"}}]}).encode()
        rows = parse_document(body, "application/json", "https://www.openstreetmap.org/")
        self.assertTrue(any(r["locator"] == "https://www.openstreetmap.org/node/4" for r in rows))
        self.assertNotIn("secret", json.dumps(rows))

    def test_detail_urls_from_every_format_are_not_expanded(self):
        for locator in ("https://garage.example/car/1", "https://other.example/vehicle/2",
                        "https://garage.example/%63ar/3", "https://garage.example/#/car/4"):
            for body, mime in ((json.dumps({"url": locator}).encode(), "application/json"),
                               (f"<urlset><url><loc>{locator}</loc></url></urlset>".encode(), "application/xml"),
                               (f'<a href="{locator}">Fixture</a>'.encode(), "text/html")):
                with self.subTest(locator=locator, mime=mime):
                    rows = parse_document(body, mime, task()["locator"])
                    self.assertFalse(any(t["locator"] == locator for t in expansion_tasks(task(), rows)))
                    self.assertTrue(any(r["signals"].get("detail_link_hints") or
                                        r["signals"].get("listing_link_count") for r in rows))

    def test_frontier_defends_against_legacy_unclassified_rows(self):
        for locator in ("https://garage.example/car/1", "https://garage.example/photo.jpg?size=big"):
            rows = [dict(locator=locator, kind="unknown", relation="outbound_link", label="", signals={})]
            with self.subTest(locator=locator):
                self.assertEqual(expansion_tasks(task(), rows), [])

    def test_registry_identity_does_not_erase_owned_website(self):
        body = json.dumps({"elements": [{"type": "node", "id": 1,
            "tags": {"shop": "car", "name": "Fixture", "website": "https://garage.example/"}}]}).encode()
        rows = parse_document(body, "application/json", "https://www.openstreetmap.org/")
        self.assertTrue(any(r["locator"] == "https://garage.example/" and
                            r["kind"] == "professional_seller" for r in rows))

    def test_parts_paths_are_excluded_for_sitemaps_and_spa(self):
        for locator in ("https://other.example/parts/stock", "https://other.example/#/parts/stock"):
            rows = parse_document(f'<urlset><url><loc>{locator}</loc></url></urlset>'.encode(),
                                  "application/xml", task()["locator"])
            self.assertEqual(expansion_tasks(task(), rows), [])
            self.assertEqual(rows[0]["signals"]["exclusion_reason"], "parts_surface")

    def test_large_sitemap_preserves_evidence_with_bounded_hints_and_explicit_debt(self):
        body = ("<urlset>" + "".join(f"<url><loc>https://dealer.example/car/{i}</loc></url>" for i in range(700)) + "</urlset>").encode()
        rows, observations, _ = observations_from_document(body, "application/xml", task()["locator"],
            policy_ref="audit:fixture", evidence_group="fixture", observed_at="2026-09-01T00:00:00Z",
            expires_at="2026-10-01T00:00:00Z")
        self.assertTrue(observations)
        self.assertTrue(any(r["signals"].get("detail_hints_truncated") for r in rows))
        self.assertEqual(rows[0]["signals"]["detail_links_encountered"], 700)
        self.assertTrue(all(len(json.dumps(o.signals)) < 16384 for o in observations))
        self.assertEqual(expansion_tasks(task(), rows), [])


if __name__ == "__main__":
    unittest.main()
