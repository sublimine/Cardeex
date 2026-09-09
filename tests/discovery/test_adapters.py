"""Replay parser regression fixtures are synthetic and contain no stock records."""
import importlib
import json
import unittest


class AdapterTests(unittest.TestCase):
    def setUp(self):
        try:
            self.a=importlib.import_module("discovery.adapters")
        except ModuleNotFoundError:
            self.fail("Discovery replay adapters have not been implemented")

    def parse(self,text,kind="text/html",base="https://dealer.example/"):
        return self.a.parse_document(text.encode("utf-8"),kind,base)

    def test_three_vehicle_page_hands_off_surface_without_fetching_details(self):
        rows=self.parse('<h1>Garage du village</h1><a href="/vehicle/1">Renault Clio</a><a href="/vehicle/2">Citroën C3</a><a href="/vehicle/3">Peugeot 208</a>')
        surface=[r for r in rows if r["relation"]=="inventory_surface" and r["locator"]=="https://dealer.example/"]
        self.assertEqual(len(surface),1)
        self.assertEqual(surface[0]["kind"],"source")
        self.assertEqual(surface[0]["signals"]["listing_link_count"],3)
        self.assertFalse(any(r["kind"] in ("professional_seller","point_of_sale") for r in rows))

    def test_inventory_facets_preserved_duplicates_deduplicated(self):
        rows=self.parse('<a href="/stock?class=car">Cars</a><a href="/stock?class=lcv">Vans</a><a href="/stock?class=car">Cars again</a><a href="https://bücher.example/">Über uns</a>')
        locators=[r["locator"] for r in rows]
        self.assertEqual(locators.count("https://dealer.example/stock?class=car"),1)
        self.assertIn("https://dealer.example/stock?class=lcv",locators)
        self.assertIn("https://xn--bcher-kva.example/",locators)

    def test_spa_dealer_routes_retain_distinct_locators(self):
        rows=self.parse('<a href="/#/dealer/a">Dealer A</a><a href="/#/dealer/b">Dealer B</a>')
        self.assertEqual([r["locator"] for r in rows],["https://dealer.example/#/dealer/a","https://dealer.example/#/dealer/b"])

    def test_jsonld_graph_separates_business_and_location(self):
        graph={"@graph":[{"@type":"AutoDealer","@id":"#org","name":"Autos André","url":"/","address":{"@type":"PostalAddress","streetAddress":"Rue Test 2","addressCountry":"FR"}},{"@type":"LocalBusiness","name":"Repair only","url":"/repair"},{"@type":"MotorcycleDealer","name":"Moto","url":"/moto"}]}
        rows=self.parse('<script type="application/ld+json">'+json.dumps(graph)+'</script>')
        self.assertEqual(sum(r["kind"]=="professional_seller" for r in rows),2)
        self.assertEqual(sum(r["kind"]=="point_of_sale" for r in rows),1)
        self.assertTrue(any(r["locator"].endswith("/repair") and r["kind"]=="unknown" for r in rows))
        self.assertFalse(any("market_country" in r["signals"] for r in rows))

    def test_dealerless_portal_does_not_create_dealer(self):
        rows=self.parse('<script type="application/ld+json">{"@type":"WebSite","name":"Motor Portal","url":"/"}</script><a href="/stock">Search cars</a>')
        self.assertTrue(rows)
        self.assertFalse(any(r["kind"] in ("professional_seller","point_of_sale") for r in rows))

    def test_jsonld_subject_uses_safe_url_boundary_without_leaking_secrets(self):
        for subject in ("https://dealer.example/?access_token=sample-secret",
                        "https://dealer.example/#/org?access_token=sample-secret",
                        "https://sample-secret@dealer.example/", "https://127.0.0.1/"):
            with self.subTest(subject=subject):
                rows=self.parse(json.dumps({"@type":"AutoDealer", "url":"/", "@id":subject}), "application/ld+json")
                self.assertEqual(len(rows),1)
                self.assertNotIn("schema_subject", rows[0]["signals"])
                self.assertNotIn("sample-secret", json.dumps(rows))
        rows=self.parse(json.dumps({"@type":"AutoDealer", "url":"/", "@id":"#organization"}), "application/ld+json")
        self.assertEqual(rows[0]["signals"]["schema_subject"], "https://dealer.example/#organization")

    def test_parts_and_login_pages_do_not_assert_inventory_or_zero(self):
        rows=self.parse('<h1>Spare parts</h1><a href="/parts/vehicle/1">Engine</a><a href="/parts/vehicle/2">Gearbox</a>')
        self.assertFalse(any(r["relation"]=="inventory_surface" for r in rows))
        rows=self.parse('<h1>Login required</h1><form><input type="password"></form>')
        self.assertEqual(rows[0]["signals"]["access_state"],"login_required")
        self.assertNotIn("inventory_count",rows[0]["signals"])

    def test_sitemap_and_index_safe_locators_only(self):
        rows=self.parse('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://dealer.example/stock?class=car&amp;page=2</loc></url></urlset>',"application/xml")
        self.assertEqual(rows[0]["relation"],"sitemap_page")
        self.assertTrue(rows[0]["locator"].endswith("class=car&page=2"))
        index=self.parse('<sitemapindex><sitemap><loc>/sitemap-stock.xml</loc></sitemap></sitemapindex>',"text/xml")
        self.assertTrue(index[0]["signals"]["sitemap_index"])

    def test_xml_entities_dtd_and_malformed_rejected(self):
        for value in ('<!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///etc/passwd">]><urlset>&x;</urlset>','<urlset><url></urlset>'):
            with self.subTest(value=value),self.assertRaises(self.a.ParserError):
                self.parse(value,"application/xml")

    def test_osm_commoncrawl_jsonl_and_simple_rows(self):
        rows=self.parse(json.dumps({"elements":[{"type":"node","id":12,"tags":{"shop":"car","name":"Small garage","website":"https://small.example/","addr:country":"CH"}}]}),"application/json")
        self.assertTrue(any(r["kind"]=="professional_seller" for r in rows))
        self.assertTrue(any(r["signals"].get("osm_element")=="node/12" for r in rows))
        rows=self.parse('{"url":"http://old.example/stock","timestamp":"20260901000000","status":"200"}\n{"url":"https://new.example/","name":"New"}',"application/x-ndjson")
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["locator"],"http://old.example/stock")
        self.assertEqual(rows[0]["signals"]["archive_timestamp"],"20260901000000")

    def test_limits_and_unreadable_documents_are_explicit(self):
        for body,kind in ((b"","text/html"),(b"photo","image/jpeg"),(b'{"bad":',"application/json"),(b"\x00\xff","text/html")):
            with self.subTest(kind=kind),self.assertRaises(self.a.ParserError):
                self.a.parse_document(body,kind,"https://dealer.example/")
        with self.assertRaises(self.a.ParserLimitError):
            self.a.parse_document(b"x"*(self.a.MAX_DOCUMENT_BYTES+1),"text/html","https://dealer.example/")
        with self.assertRaises(self.a.ParserLimitError):
            self.parse("<a href='/stock'>Stock</a>"*(self.a.MAX_NODES+1))
        with self.assertRaises(self.a.ParserLimitError):
            self.parse("["*(self.a.MAX_DEPTH+1)+"0"+"]"*(self.a.MAX_DEPTH+1),"application/json")

    def test_hostile_links_never_become_candidates(self):
        rows=self.parse('<a href="javascript:alert(1)">go</a><a href="http://127.0.0.1/">private</a><a href="https://safe.example/?token=secret">private</a><a href="/safe">safe</a>')
        self.assertEqual([r["locator"] for r in rows],["https://dealer.example/safe"])

    def test_parts_stock_does_not_become_vehicle_inventory(self):
        rows=self.parse('<h1>Spare parts warehouse</h1><a href="/stock">Current stock</a>')
        self.assertFalse(any(r["relation"]=="inventory_surface" for r in rows))

    def test_parts_footer_does_not_erase_three_car_surface(self):
        rows=self.parse('<h1>Garage du village</h1><a href="/car/1">one</a><a href="/car/2">two</a><a href="/car/3">three</a><footer><a href="/parts">Parts</a></footer>')
        self.assertTrue(any(r["signals"].get("listing_link_count")==3 for r in rows))

    def test_closed_and_restricted_states_remain_explicit(self):
        for text,state in (("Permanently closed","closed_claim"),("Access denied","restricted")):
            rows=self.parse(f"<h1>{text}</h1><a href='/stock'>Stock</a>")
            self.assertFalse(any(r["relation"]=="inventory_surface" for r in rows))
            self.assertTrue(any(r["signals"].get("access_state")==state for r in rows))

    def test_json_duplicate_keys_and_invalid_jsonld_are_explicit_errors(self):
        for text,kind in (('{"url":"https://one.example/","url":"https://two.example/"}',"application/json"),('<script type="application/ld+json">{broken}</script>',"text/html"),('{"url":"https://one.example/","value":NaN}',"application/json")):
            with self.subTest(kind=kind),self.assertRaises(self.a.ParserError):
                self.parse(text,kind)

    def test_evidence_selectors_survive_each_adapter(self):
        examples=(("<a href='/stock'>Stock</a>","text/html"),('{"url":"https://dealer.example/"}',"application/json"),('<urlset><url><loc>/stock</loc></url></urlset>',"application/xml"))
        for text,kind in examples:
            for row in self.parse(text,kind):
                self.assertTrue(row["signals"]["evidence_selector"])
                self.assertTrue(row["signals"]["assertion_method"])
                self.assertEqual(row["signals"]["parser_version"],self.a.PARSER_VERSION)

    def test_candidate_limit_is_not_silent_truncation(self):
        from unittest.mock import patch
        with patch.object(self.a,"MAX_CANDIDATES",2):
            with self.assertRaises(self.a.ParserLimitError):
                self.parse('<a href="/one">one</a><a href="/two">two</a><a href="/three">three</a>')

    def test_media_hrefs_are_not_discovery_queue_candidates(self):
        rows=self.parse('<a href="/photos/car.jpg?size=large">Photo</a><a href="/video.mp4">Video</a><a href="/stock">Stock</a>')
        self.assertEqual([r["locator"] for r in rows],["https://dealer.example/stock"])
        rows=self.parse('[{"url":"https://dealer.example/photo.webp"},{"url":"https://dealer.example/"}]',"application/json")
        self.assertEqual([r["locator"] for r in rows],["https://dealer.example/"])


if __name__=="__main__":
    unittest.main()
