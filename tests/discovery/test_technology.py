"""Synthetic documents exercise observational signatures without fetching sites."""

import importlib.util
import json
import unittest


class TechnologyTests(unittest.TestCase):
    def classify(self, html, content_type="text/html", origin="https://garage.example/", headers=None):
        self.assertIsNotNone(importlib.util.find_spec("discovery.technology"), "technology classifier is absent")
        from discovery.technology import classify_technology
        return classify_technology(html.encode() if isinstance(html, str) else html, content_type, origin, headers)

    @staticmethod
    def names(result):
        return {item["name"] for item in result["detections"]}

    def test_cms_generator_and_observed_plugin_are_traceable(self):
        result = self.classify('<meta name="generator" content="WordPress 6.9">'
                               '<script src="/wp-content/plugins/synthetic-stock/public.js"></script>')
        self.assertIn("WordPress", self.names(result))
        self.assertIn("wordpress-plugin:synthetic-stock", self.names(result))
        for detection in result["detections"]:
            self.assertTrue(detection["evidence"])
            self.assertTrue(detection["source_refs"])
            self.assertIn(detection["confidence"], ("strong_signal", "tentative"))
            self.assertTrue(detection["evidence"][0]["selector"])
        self.assertEqual(result["inventory_surfaces"], [])

    def test_drupal_header_and_joomla_generator_remain_multiple(self):
        result = self.classify('<meta name="generator" content="Joomla! - Open Source Content Management">',
                               headers={"X-Generator": "Drupal 11 (https://www.drupal.org)"})
        self.assertEqual(self.names(result), {"Joomla", "Drupal"})
        self.assertTrue(result["conflicts"])
        self.assertTrue(all(item["confidence"] == "tentative" for item in result["detections"]))

    def test_next_and_nuxt_asset_tags_identify_frameworks_without_certifying_js(self):
        for path, name in (("/_next/static/chunks/app.js", "Next.js"), ("/_nuxt/app.js", "Nuxt")):
            with self.subTest(name=name):
                result = self.classify(f'<main>Our three available vehicles</main><script src="{path}"></script>')
                self.assertIn(name, self.names(result))
                self.assertEqual(result["dynamic_rendering_required"], "unknown")

    def test_powered_by_header_is_observed_without_echoing_unrelated_headers(self):
        result = self.classify("<html></html>", headers={"x-powered-by": "Next.js", "Set-Cookie": "secret-value"})
        self.assertIn("Next.js", self.names(result))
        self.assertNotIn("secret-value", json.dumps(result))

    def test_empty_js_shell_is_explicit_debt(self):
        result = self.classify('<div id="__nuxt"></div><script src="/_nuxt/app.js"></script>')
        self.assertIs(result["dynamic_rendering_required"], True)
        self.assertIn("dynamic_shell_unrendered", result["limitations"])

    def test_framework_root_with_static_content_is_not_empty_shell(self):
        result = self.classify('<div id="__next"><a href="/stock">Our available vehicles</a></div>'
                               '<script src="/_next/static/app.js"></script>')
        self.assertEqual(result["dynamic_rendering_required"], "unknown")

    def test_escaped_narrative_comments_and_inert_code_are_not_scripts(self):
        result = self.classify('<p>WordPress Drupal Next.js AutoScout24 /_next/static/a.js</p>'
                               '&lt;script src="/_nuxt/app.js"&gt;&lt;/script&gt;'
                               '<!-- <meta name="generator" content="WordPress"> -->'
                               '<template><script src="/_next/static/app.js"></script></template>'
                               '<textarea><iframe src="https://www.autoscout24.de/stock"></iframe></textarea>'
                               '<script>const text = "<meta name=generator content=WordPress>";</script>')
        self.assertEqual(result["detections"], [])
        self.assertEqual(result["inventory_surfaces"], [])

    def test_autoscout_embedded_provider_and_only_observed_stock_surface(self):
        url = "https://www.autoscout24.de/haendler/example/stock?sort=price"
        result = self.classify(f'<iframe src="{url}" title="Fahrzeugbestand"></iframe>')
        self.assertIn("AutoScout24", self.names(result))
        self.assertEqual([s["locator"] for s in result["inventory_surfaces"]], [url])
        self.assertTrue(result["inventory_surfaces"][0]["requires_access_review"])

    def test_provider_link_is_not_proof_of_installed_inventory_software(self):
        result = self.classify('<a href="https://www.autoscout24.de/">AutoScout24</a>'
                               '<script src="https://www.autoscout24.de.evil.example/widget.js"></script>')
        self.assertNotIn("AutoScout24", self.names(result))

    def test_dealerwebs_observed_embed_is_a_provider_hint(self):
        result = self.classify('<iframe src="https://embed.dealerwebs.com/stock/example"></iframe>')
        self.assertIn("DealerWebs", self.names(result))
        self.assertEqual(len(result["inventory_surfaces"]), 1)

    def test_unknown_embedded_stock_is_preserved_without_invented_provider(self):
        url = "https://inventory-host.example/stock/list?branch=3"
        result = self.classify(f'<iframe src="{url}" title="Our vehicles"></iframe><a href="/inventory">Stock</a>')
        self.assertEqual(result["detections"], [])
        self.assertEqual({s["locator"] for s in result["inventory_surfaces"]}, {url, "https://garage.example/inventory"})

    def test_static_stock_link_does_not_prove_zero_or_complete_inventory(self):
        result = self.classify('<a href="/stock">Stock</a>')
        self.assertEqual(result["dynamic_rendering_required"], "unknown")
        self.assertIn("technology_does_not_certify_inventory_or_entity", result["limitations"])

    def test_hostile_and_secret_urls_are_omitted_without_leaking(self):
        urls = ["http://127.0.0.1/stock", "http://169.254.169.254/stock", "http://server.internal/stock",
                "https://user:secret-value@example.com/stock", "javascript:alert(1)",
                "https://www.autoscout24.de/stock?access_token=secret-value", "file:///stock"]
        result = self.classify("".join(f'<iframe src="{url}"></iframe>' for url in urls))
        self.assertEqual(result["inventory_surfaces"], [])
        self.assertEqual(result["detections"], [])
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertIn("unsafe_or_secret_locator_omitted", result["limitations"])

    def test_login_error_and_closed_claims_do_not_classify_business_activity(self):
        for body in ('<input type="password"><a href="/stock">Stock</a>', '<h1>Access denied</h1>',
                     '<h1>Service unavailable</h1>', '<h1>Permanently closed</h1>'):
            with self.subTest(body=body):
                result = self.classify(body)
                self.assertIn("access_or_error_content_activity_unknown", result["limitations"])
                self.assertNotIn("business_active", result)
                self.assertEqual(result["dynamic_rendering_required"], "unknown")

    def test_unsupported_formats_return_unknown_without_throwing(self):
        for content_type, body in (("application/json", b'{"generator":"WordPress"}'),
                                   ("application/xml", b'<root/>'), ("text/plain", b'WordPress'),
                                   ("image/png", b'\x00\xff')):
            with self.subTest(content_type=content_type):
                result = self.classify(body, content_type)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["detections"], [])

    def test_input_output_and_node_limits_are_explicit(self):
        result = self.classify(b"x" * (2 * 1024 * 1024 + 1))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("document_byte_limit", result["limitations"])
        result = self.classify("<div></div>" * 10001)
        self.assertIn("document_node_limit", result["limitations"])
        result = self.classify("".join(f'<a href="/stock/{i}">stock</a>' for i in range(100)))
        self.assertLessEqual(len(result["inventory_surfaces"]), 8)
        self.assertIn("signal_limit", result["limitations"])
        self.assertLessEqual(len(json.dumps(result)), 16000)

    def test_duplicate_ambiguous_attributes_do_not_select_a_silent_winner(self):
        result = self.classify('<script src="/_next/static/a.js" src="/_nuxt/b.js"></script>')
        self.assertEqual(result["detections"], [])
        self.assertIn("ambiguous_duplicate_attributes", result["limitations"])

    def test_empty_invalid_encoding_and_bad_origin_return_explicit_unknown(self):
        for body, content_type, origin in ((b"", "text/html", "https://garage.example/"),
                                            (b"\xff", "text/html", "https://garage.example/"),
                                            (b"<html/>", "text/html", "http://127.0.0.1/")):
            with self.subTest(body=body, origin=origin):
                result = self.classify(body, content_type, origin)
                self.assertEqual(result["status"], "unknown")
                self.assertTrue(result["limitations"])

    def test_document_base_resolves_only_observed_relative_surfaces(self):
        for body in ('<base href="https://inventory-host.example/branch/"><a href="stock">Stock</a>',
                     '<a href="stock">Stock</a><base href="https://inventory-host.example/branch/">'):
            with self.subTest(body=body):
                result = self.classify(body)
                self.assertEqual([row["locator"] for row in result["inventory_surfaces"]],
                                 ["https://inventory-host.example/branch/stock"])

    def test_unsafe_base_does_not_silently_rebase_to_document_origin(self):
        result = self.classify('<base href="http://127.0.0.1/"><a href="stock">Stock</a>'
                               '<a href="https://public.example/stock">Vehicles</a>')
        self.assertEqual([row["locator"] for row in result["inventory_surfaces"]], ["https://public.example/stock"])
        self.assertIn("unresolved_document_base", result["limitations"])

    def test_named_root_and_unrelated_script_do_not_prove_framework_rendering(self):
        result = self.classify('<div id="__nuxt"></div><script src="/analytics.js"></script>')
        self.assertEqual(result["dynamic_rendering_required"], "unknown")

    def test_server_rendered_media_is_not_an_empty_js_shell(self):
        result = self.classify('<div id="__next"><img src="/car.jpg" alt=""></div>'
                               '<script src="/_next/static/app.js"></script>')
        self.assertEqual(result["dynamic_rendering_required"], "unknown")

    def test_full_signal_budget_fits_observation_storage_and_is_json_serializable(self):
        body = "".join(f'<script src="/wp-content/plugins/plugin{i}/' + "a" * 80 + f'{suffix}.js"></script>'
                       for i in range(15) for suffix in ("first", "second", "third"))
        body += "".join('<iframe src="https://inventory.example/stock/' + "a" * 120 + f'{i}"></iframe>' for i in range(20))
        result = self.classify(body)
        self.assertLessEqual(len(json.dumps(result)), 15000)
        self.assertLessEqual(len(result["detections"]), 10)
        self.assertLessEqual(len(result["inventory_surfaces"]), 8)

    def test_nonexecuting_script_type_cannot_supply_framework_or_provider(self):
        result = self.classify('<script type="text/plain" src="/_next/static/app.js"></script>'
                               '<script type="application/json" src="https://www.autoscout24.de/widget.js"></script>')
        self.assertEqual(result["detections"], [])

    def test_html_trailing_slash_does_not_close_inert_nonvoid_elements(self):
        for tag in ("template", "script", "textarea", "iframe"):
            with self.subTest(tag=tag):
                result = self.classify(f'<{tag}/><meta name="generator" content="WordPress">')
                self.assertEqual(result["detections"], [])

    def test_rawtext_elements_do_not_promote_nested_markup_to_signatures(self):
        for tag in ("xmp", "noembed", "noframes", "plaintext"):
            with self.subTest(tag=tag):
                result = self.classify(f'<{tag}><script src="/_next/static/a.js"></script>'
                                       '<meta name="generator" content="WordPress">')
                self.assertEqual(result["detections"], [])

    def test_plaintext_consumes_even_an_apparent_closing_tag(self):
        result = self.classify('<plaintext></plaintext><meta name="generator" content="WordPress">')
        self.assertEqual(result["detections"], [])

    def test_explicit_inert_close_still_allows_later_active_generator(self):
        result = self.classify('<template/><meta name="generator" content="Drupal"></template>'
                               '<meta name="generator" content="WordPress">')
        self.assertEqual(self.names(result), {"WordPress"})

    def test_inert_nesting_has_a_structural_depth_limit(self):
        result = self.classify('<template>' + '<div>' * 1000 + '</not>' * 5000)
        self.assertEqual(result["status"], "partial")
        self.assertIn("document_depth_limit", result["limitations"])

    def test_unmatched_closing_tags_consume_the_parser_event_budget(self):
        result = self.classify('<template>' + '</not>' * 10001)
        self.assertEqual(result["status"], "partial")
        self.assertIn("document_node_limit", result["limitations"])

    def test_comments_also_consume_the_parser_event_budget(self):
        result = self.classify('<!-- ignored -->' * 10001)
        self.assertEqual(result["status"], "partial")
        self.assertIn("document_node_limit", result["limitations"])

    def test_partial_parse_preserves_conflicts_and_downgrades_confidence(self):
        prefix = '<meta name="generator" content="WordPress"><meta name="generator" content="Drupal">'
        for suffix in ('<!--a-->' * 10001, '<template>' + '<div>' * 1000):
            with self.subTest(suffix=suffix[:20]):
                result = self.classify(prefix + suffix)
                self.assertEqual(result["status"], "partial")
                self.assertTrue(result["conflicts"])
                self.assertTrue(all(item["confidence"] == "tentative" for item in result["detections"]))

    def test_malformed_declaration_preserves_prior_conflicts_without_throwing(self):
        html = '<meta name="generator" content="WordPress"><meta name="generator" content="Drupal"><![invalid]>'
        try:
            result = self.classify(html)
        except Exception as exc:
            self.fail(f"Malformed HTML escaped the classification boundary: {type(exc).__name__}")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["conflicts"])
        self.assertTrue(all(item["confidence"] == "tentative" for item in result["detections"]))


if __name__ == "__main__":
    unittest.main()
