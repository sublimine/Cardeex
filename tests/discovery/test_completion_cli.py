"""CLI checks use synthetic local files and temporary databases only."""

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from discovery.cli import main
from discovery.geography import load_catalogue
from discovery.profiles import load_profiles
from discovery.store import Store


class CompletionCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = self.path / "state" / "cli.sqlite"
        self.now = datetime.now(timezone.utc)
        self.observed = (self.now - timedelta(hours=1)).isoformat()
        self.expires = (self.now + timedelta(days=30)).isoformat()

    def write_json(self, name, data):
        path = self.path / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                code = main(["--db", str(self.db), *arguments])
            except SystemExit as exc:
                code = exc.code
        return code, output.getvalue(), errors.getvalue()

    def success(self, *arguments):
        code, output, error = self.invoke(*arguments)
        self.assertEqual((code, error), (0, ""))
        return json.loads(output)

    def catalogue(self):
        body = json.dumps({"units": [{"code": "00001", "name": "Synthetic", "aliases": ["Local Alias"]}]}).encode()
        metadata = {"country": "ES", "version": "synthetic-1", "source_url": "https://catalogue.example/units.json",
                    "observed_at": self.observed, "body_sha256": hashlib.sha256(body).hexdigest()}
        return body, metadata, load_catalogue(body, metadata)

    def binding(self):
        return {"adapter": "searxng_json", "endpoint": "https://search.example/search",
                "assessment_ref": "synthetic:assessment", "countries": ["ES"],
                "vehicle_classes": ["car", "lcv", "motorcycle", "motorhome"],
                "parameters": {}, "max_pages": 2}

    def schedule_spec(self):
        profile = load_profiles()["ES"]
        profile["strategies"] = [profile["strategies"][0]]
        return {"schedule_id": "synthetic-calendar", "profiles": {"ES": profile}, "localities": [],
                "channels": {}, "starts_at": "2099-01-01T00:00:00Z", "interval_seconds": 86400}

    def test_geo_import_json_and_csv_are_pure_and_keep_original_codes(self):
        body, metadata, expected = self.catalogue()
        source = self.path / "units.json"
        source.write_bytes(body)
        meta = self.write_json("metadata.json", metadata)
        with patch("discovery.cli.Store", side_effect=AssertionError("pure command opened Store")):
            actual = self.success("geo-import", "--file", str(source), "--metadata", meta, "--format", "json")
        self.assertEqual(actual, expected)
        source = self.path / "units.csv"
        body = b"code,name\n00002,Synthetic CSV\n"
        source.write_bytes(body)
        metadata["body_sha256"] = hashlib.sha256(body).hexdigest()
        meta = self.write_json("csv-metadata.json", metadata)
        actual = self.success("geo-import", "--file", str(source), "--metadata", meta, "--format", "csv")
        self.assertEqual(actual["units"][0]["code"], "00002")
        self.assertIsNone(actual["completeness"]["coverage_ratio"])
        self.assertFalse(self.db.parent.exists())

    def test_geo_resolve_preserves_query_and_does_not_open_database(self):
        _, _, catalogue = self.catalogue()
        path = self.write_json("catalogue.json", catalogue)
        with patch("discovery.cli.Store", side_effect=AssertionError("pure command opened Store")):
            result = self.success("geo-resolve", "--catalogue", path, "--name", " local alias ")
        self.assertEqual(result["code"], "00001")
        self.assertEqual(result["query_original"]["name"], " local alias ")
        self.assertFalse(self.db.exists())

    def test_normalized_catalogue_uses_its_serialized_limit_not_original_raw_limit(self):
        _, _, catalogue = self.catalogue()
        source = self.path / "catalogue-large-encoding.json"
        source.write_bytes(b" " * (16 * 1024 * 1024 + 1) + json.dumps(catalogue).encode())
        result = self.success("geo-resolve", "--catalogue", str(source), "--name", "Synthetic")
        self.assertEqual(result["code"], "00001")

    def test_technology_accepts_inline_headers_without_fetching_or_opening_store(self):
        source = self.path / "technology.html"
        source.write_text('<html><head></head><body>Fixture</body></html>', encoding="utf-8")
        with patch("discovery.cli.Store", side_effect=AssertionError("pure command opened Store")), \
             patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = self.success("technology", "--file", str(source), "--origin", "https://garage.example/",
                                  "--content-type", "text/html", "--headers", '{"X-Generator":"WordPress 6.9"}')
        self.assertTrue(any(row["name"] == "WordPress" for row in result["detections"]))
        self.assertFalse(self.db.exists())

    def test_estimate_returns_unknown_without_groups_and_never_opens_store(self):
        payload = {"records": [], "independent_groups": [], "now": self.now.isoformat(),
                   "stratum": {"market_country": "ES", "vehicle_class": "car", "source_type": "dealer_owned",
                               "entity_kind": "point_of_sale", "universe_version": "synthetic-1",
                               "window_start": (self.now - timedelta(days=1)).isoformat(),
                               "window_end": self.observed}}
        path = self.write_json("estimate.json", payload)
        with patch("discovery.cli.Store", side_effect=AssertionError("pure command opened Store")):
            result = self.success("estimate", "--file", path)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["coverage_ratio"])
        self.assertFalse(self.db.exists())

    def test_pure_commands_preserve_existing_database_bytes_and_mtime(self):
        body, metadata, catalogue = self.catalogue()
        source = self.path / "units.json"
        source.write_bytes(body)
        meta = self.write_json("metadata.json", metadata)
        imported = self.write_json("catalogue.json", catalogue)
        html = self.path / "observed.html"
        html.write_text("<html><body>Synthetic</body></html>", encoding="utf-8")
        with Store(self.db):
            pass
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.db.parent.iterdir()}
        for arguments in (("geo-import", "--file", str(source), "--metadata", meta),
                          ("geo-resolve", "--catalogue", imported, "--name", "Synthetic"),
                          ("technology", "--file", str(html), "--origin", "https://garage.example/", "--content-type", "text/html")):
            with self.subTest(command=arguments[0]), \
                 patch("discovery.cli.load_profiles", side_effect=AssertionError("pure command depends on profiles")), \
                 patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                self.success(*arguments)
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.db.parent.iterdir()}
        self.assertEqual(before, after)

    def test_plan_accepts_channel_mapping_and_alternative_catalogue(self):
        _, _, catalogue = self.catalogue()
        path = self.write_json("catalogue.json", catalogue)
        strategy = load_profiles()["ES"]["strategies"][0]["id"]
        channels = self.write_json("channels.json", {strategy: self.binding()})
        result = self.success("plan", "--countries", "ES", "--catalogue", path, "--channels", channels,
                              "--epoch", "synthetic:2099", "--limit", "2")
        self.assertEqual(result["channels"][strategy]["adapter"], "searxng_json")
        self.assertEqual(result["tasks_enqueued"], 2)
        self.assertFalse(result["network_executed"])
        code, _, _ = self.invoke("plan", "--catalogue", path, "--localities", path, "--epoch", "bad")
        self.assertEqual(code, 2)

    def test_channel_parameters_change_plan_identity_and_malformed_mapping_is_rejected(self):
        strategy = load_profiles()["ES"]["strategies"][0]["id"]
        binding = self.binding()
        path = self.write_json("channels.json", {strategy: binding})
        args = ("plan", "--countries", "ES", "--channels", path, "--epoch", "synthetic:identity", "--limit", "1")
        first = self.success(*args)
        binding["max_pages"] = 3
        self.write_json("channels.json", {strategy: binding})
        second = self.success(*args)
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertNotEqual(first["channels_sha256"], second["channels_sha256"])
        self.write_json("channels.json", [])
        code, output, error = self.invoke(*args)
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("operation", json.loads(error))

    def test_schedule_show_does_not_create_database_or_touch_existing_files(self):
        with patch("discovery.cli.Store", side_effect=AssertionError("read-only inspection opened Store")):
            result = self.success("schedule", "show")
        self.assertEqual(result["schedules"], [])
        self.assertTrue(result["read_only"])
        self.assertFalse(self.db.parent.exists())
        with Store(self.db):
            pass
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.db.parent.iterdir()}
        with patch("discovery.cli.Store", side_effect=AssertionError("read-only inspection opened Store")):
            self.success("schedule", "show")
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.db.parent.iterdir()}
        self.assertEqual(before, after)

    def test_schedule_register_tick_pause_resume_and_frozen_definition(self):
        spec = self.schedule_spec()
        path = self.write_json("schedule.json", spec)
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = self.success("schedule", "register", "--spec", path, "--actor", "synthetic-reviewer")
            self.assertTrue(result["created"])
            self.assertEqual(self.success("schedule", "tick", "--now", "2098-12-31T23:59:59Z")["tasks_generated"], 0)
            self.success("schedule", "pause", "--id", spec["schedule_id"], "--actor", "reviewer", "--reason", "local review")
            self.assertEqual(self.success("schedule", "tick", "--now", "2099-01-01T00:00:00Z")["tasks_generated"], 0)
            self.success("schedule", "resume", "--id", spec["schedule_id"], "--actor", "reviewer", "--reason", "review complete")
            result = self.success("schedule", "tick", "--now", "2099-01-01T00:00:00Z", "--max-tasks", "1", "--max-schedules", "1")
        self.assertEqual(result["tasks_generated"], 1)
        self.assertFalse(result["network_executed"])
        spec["interval_seconds"] = 3600
        self.write_json("schedule.json", spec)
        self.assertEqual(self.invoke("schedule", "register", "--spec", path, "--actor", "reviewer")[0], 2)

    def test_new_commands_sanitize_bad_source_input(self):
        marker = "synthetic-sensitive-marker"
        source = self.path / "invalid.json"
        source.write_text('{"' + marker + '":}', encoding="utf-8")
        for args in (("geo-resolve", "--catalogue", str(source), "--name", "Local"),
                     ("estimate", "--file", str(source)),
                     ("schedule", "register", "--spec", str(source), "--actor", "reviewer")):
            with self.subTest(command=args[0]):
                code, output, errors = self.invoke(*args)
                self.assertEqual(code, 2)
                self.assertNotIn(marker, output + errors)
                self.assertEqual(output, "")
                self.assertTrue(errors.lstrip().startswith("{"), "structured sanitized error not implemented")
                self.assertIn("operation", json.loads(errors))

    def test_channel_replay_and_resume_dispatch_validated_bindings_without_network(self):
        binding = self.binding()
        path = self.write_json("binding.json", binding)
        source = self.write_json("search.json", {"results": []})
        calls = []

        def inspect_channel(store, body, content_type, origin, *, binding, policy_ref, observed_at, expires_at):
            self.assertEqual(json.loads(body), {"results": []})
            self.assertEqual(content_type, "application/json")
            self.assertEqual(origin, "https://search.example/search")
            self.assertEqual(binding["assessment_ref"], policy_ref)
            self.assertTrue(observed_at and expires_at)
            calls.append("inspect")
            return {"candidate_ids": [], "coverage_ratio": None}

        def resume_channel(store, task_key, binding, *, actor, reason):
            self.assertEqual((task_key, actor, reason), ("synthetic-task", "reviewer", "approved binding"))
            self.assertEqual(binding["adapter"], "searxng_json")
            calls.append("resume")
            return {"resumed": task_key, "network_executed": False}

        # Core is integrated independently; these doubles check the agreed CLI boundary only.
        with patch("discovery.engine.inspect_channel", side_effect=inspect_channel, create=True), \
             patch("discovery.engine.resume_channel", side_effect=resume_channel, create=True), \
             patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = self.success("inspect-channel", "--file", source, "--binding", path,
                                  "--origin", "https://search.example/search", "--content-type", "application/json",
                                  "--policy-ref", binding["assessment_ref"], "--observed-at", self.observed,
                                  "--expires-at", self.expires)
            self.assertIsNone(result["coverage_ratio"])
            result = self.success("resume-channel", "--task", "synthetic-task", "--binding", path,
                                  "--actor", "reviewer", "--reason", "approved binding")
            self.assertFalse(result["network_executed"])
        self.assertEqual(calls, ["inspect", "resume"])

    def test_channel_replay_rejects_mismatched_assessment_before_opening_store(self):
        path = self.write_json("binding.json", self.binding())
        source = self.write_json("search.json", {"results": []})
        with patch("discovery.cli.Store", side_effect=AssertionError("rejected binding opened Store")):
            code, output, error = self.invoke("inspect-channel", "--file", source, "--binding", path,
                                             "--origin", "https://search.example/search", "--content-type", "application/json",
                                             "--policy-ref", "unrelated-policy", "--observed-at", self.observed,
                                             "--expires-at", self.expires)
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertTrue(error.startswith("{"), "missing structured binding error")
        self.assertFalse(self.db.exists())

    def test_channel_replay_real_engine_preserves_observed_candidate_without_network(self):
        binding = self.binding()
        path = self.write_json("binding.json", binding)
        source = self.write_json("search.json", {"results": [{"url": "https://garage.example/", "title": "Synthetic garage"}]})
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = self.success("inspect-channel", "--file", source, "--binding", path,
                                  "--origin", "https://search.example/search?q=garage&pageno=1",
                                  "--content-type", "application/json", "--policy-ref", binding["assessment_ref"],
                                  "--observed-at", self.observed, "--expires-at", self.expires)
        self.assertEqual(len(result["candidate_ids"]), 1)
        self.assertIsNone(result["coverage_ratio"])
        with Store(self.db) as store:
            self.assertEqual(store.count_candidates(), 1)

    def test_sirene_cli_replay_ledger_candidate_review_and_export_minimize_personal_data(self):
        binding = {**self.binding(), "adapter": "sirene_csv", "endpoint": "https://registry.example/local.csv",
                   "countries": ["FR"], "parameters": {"snapshot_date": self.now.date().isoformat(),
                   "activity_codes": ["45.11Z"], "nomenclature": "NAFRev2"}}
        path = self.write_json("sirene-binding.json", binding)
        source = self.path / "synthetic-establishments.csv"
        body = ("siret,statutDiffusionEtablissement,etatAdministratifEtablissement,activitePrincipaleEtablissement,"
                "nomenclatureActivitePrincipaleEtablissement,denominationUsuelleEtablissement,nomUniteLegale\n"
                "65432198700017,O,A,45.11Z,NAFRev2,Synthetic Garage,SYNTHETIC_PERSONAL_MARKER\n"
                "65432198700025,P,A,45.11Z,NAFRev2,Restricted,SYNTHETIC_RESTRICTED_MARKER\n").encode()
        source.write_bytes(body)
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            imported = self.success("inspect-channel", "--file", str(source), "--binding", path,
                                   "--origin", binding["endpoint"], "--content-type", "text/csv",
                                   "--policy-ref", binding["assessment_ref"], "--observed-at", self.observed,
                                   "--expires-at", self.expires)
            self.assertEqual(len(imported["candidate_ids"]), 1)
            self.assertFalse(imported["raw_retained"])
            candidate_id = imported["candidate_ids"][0]
            candidate = self.success("candidate", candidate_id)
            self.assertIn("Synthetic Garage", json.dumps(candidate))
            self.success("review", "--candidate", candidate_id, "--status", "accepted", "--revision", "1",
                         "--actor", "synthetic-reviewer", "--reason", "reviewed original synthetic evidence")
            code, output, error = self.invoke("export")
        self.assertEqual((code, error), (0, ""))
        exported = [json.loads(line) for line in output.splitlines()]
        row = next(item for item in exported if item["record_type"] == "discovery_handoff_candidate")
        self.assertEqual(row["candidate"]["decision"]["status"], "accepted")
        self.assertIn("raw_withheld", output)
        self.assertNotIn("SYNTHETIC_PERSONAL_MARKER", output)
        self.assertNotIn("SYNTHETIC_RESTRICTED_MARKER", output)
        with Store(self.db) as store:
            self.assertEqual(store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
            self.assertEqual(store.channel_summary(self.now.isoformat())["pages"], 1)
            self.assertEqual(store.count_candidates(), 1)

    def test_resume_real_engine_preserves_debt_cursor_and_request_budget(self):
        from discovery.planner import build_plan
        from discovery.engine import run_worker
        from discovery.transport import AccessPolicy, FetchResult
        profile = load_profiles()["ES"]
        profile["strategies"] = [profile["strategies"][0]]
        binding = {**self.binding(), "max_pages": 1}
        plan = build_plan({"ES": profile}, [{"country": "ES", "code": "00001", "name": "Synthetic"}],
                          "synthetic:resume", max_tasks=100, channels={profile["strategies"][0]["id"]: binding})
        task = next(row for row in plan["tasks"] if row["kind"] == "query")
        policy = AccessPolicy(allowed_hosts=("search.example",), operator="synthetic-reviewer",
                              policy_ref=binding["assessment_ref"], expires_at=self.expires, min_interval_seconds=0)

        class SyntheticPage:
            def fetch(self, url):
                return FetchResult(url, 200, {"content-type": "application/json"},
                                   b'{"results":[{"url":"https://garage.example/vehicle/fixture"}]}')

        with Store(self.db) as store:
            store.save_plan({**plan, "tasks": [task]})
            run_worker(store, policy, max_tasks=1, fetcher=SyntheticPage())
            debt_key = store.db.execute("SELECT task_key FROM deferred_frontier").fetchone()[0]
            store.reserve_request(binding["assessment_ref"], binding["endpoint"], max_requests=20, min_interval=0)
            budget = store.budget_status()
        path = self.write_json("binding.json", binding)
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = self.success("resume-channel", "--task", debt_key, "--binding", path,
                                  "--actor", "synthetic-reviewer", "--reason", "approved next bounded page window")
        with Store(self.db) as store:
            tasks = store.tasks()
            self.assertEqual(store.deferred_count(), 0)
            self.assertEqual(store.budget_status(), budget)
        self.assertTrue(any(row["task_key"] == task["task_key"] for row in tasks))
        resumed = next(row for row in tasks if row["task_key"] == result["task_key"])
        self.assertEqual(resumed["status"], "pending")
        self.assertEqual(resumed["payload"]["channel_cursor"], "2")
        self.assertEqual(resumed["payload"]["channel_page"], 1)

    def test_cli_json_rejects_overflow_and_excessive_nesting_with_sanitized_error(self):
        source = self.path / "oversized-structure.json"
        for body in ('{"records":1e999}', '[' * 1100 + '"synthetic-sensitive-marker"' + ']' * 1100):
            source.write_text(body, encoding="utf-8")
            try:
                code, output, error = self.invoke("estimate", "--file", str(source))
            except RecursionError:
                self.fail("unbounded JSON nesting escaped CLI error boundary")
            self.assertEqual(code, 2)
            self.assertNotIn("synthetic-sensitive-marker", output + error)
            self.assertIn("operation", json.loads(error))

    def test_cli_strict_json_rejects_exponent_overflow(self):
        from discovery.cli import strict_json
        with self.assertRaises(ValueError):
            strict_json('{"value":1e999}')

    def test_argument_parser_does_not_echo_unrecognized_sensitive_values(self):
        code, output, error = self.invoke("estimate", "--file", "unused", "--unknown=synthetic-sensitive-marker")
        self.assertEqual(code, 2)
        self.assertNotIn("synthetic-sensitive-marker", output + error)

    def test_required_bindings_and_review_reasons_cannot_be_omitted(self):
        for arguments in (("inspect-channel", "--file", "unused"),
                          ("resume-channel", "--task", "unused"),
                          ("schedule", "pause", "--id", "unused", "--actor", "reviewer")):
            with self.subTest(command=arguments[0]):
                self.assertEqual(self.invoke(*arguments)[0], 2)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
