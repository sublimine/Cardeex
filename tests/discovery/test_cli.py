import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone


class CliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("discovery.cli"), "D5 CLI absent")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = self.path / "workbench.db"

    def invoke(self, *args):
        from discovery.cli import main
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = main(["--db", str(self.db), *args])
        return status, output.getvalue(), errors.getvalue()

    def test_profiles_do_not_create_database(self):
        code, output, _ = self.invoke("profiles")
        self.assertEqual(code, 0)
        self.assertEqual(set(json.loads(output)), {"ES", "FR", "DE", "NL", "BE", "CH"})
        self.assertFalse(self.db.exists())

    def test_plan_resume_then_coverage_keeps_unexplored_visible(self):
        code, first, error = self.invoke("plan", "--countries", "FR", "--epoch", "test:2026-09", "--limit", "2")
        self.assertEqual((code, error), (0, ""))
        data = json.loads(first)
        self.assertEqual(data["tasks_enqueued"], 2)
        code, second, _ = self.invoke("plan", "--countries", "FR", "--epoch", "test:2026-09", "--limit", "2", "--offset", "2")
        self.assertEqual(json.loads(second)["plan_id"], data["plan_id"])
        code, output, error = self.invoke("coverage", "--countries", "FR")
        self.assertEqual((code, error), (0, ""))
        report = json.loads(output)
        self.assertEqual(len(report["cells"]), 32)
        self.assertTrue(all(cell["coverage_ratio"] is None for cell in report["cells"]))

    def test_invalid_file_does_not_leak_secrets_in_errors(self):
        source = self.path / "bad.jsonl"
        source.write_text('{"locator":"https://example.com/?token=supersecret"}\n', encoding="utf-8")
        code, out, error = self.invoke("ingest", "--file", str(source))
        self.assertNotEqual(code, 0)
        self.assertNotIn("supersecret", out + error)

    def test_document_review_and_contractual_export_end_to_end(self):
        source = self.path / "dealer.html"
        source.write_text('<a href="/car/1">1</a><a href="/car/2">2</a><a href="/car/3">3</a>', encoding="utf-8")
        code, output, error = self.invoke("inspect-file", "--file", str(source), "--origin", "https://garage.example/",
            "--content-type", "text/html", "--policy-ref", "test:synthetic", "--evidence-group", "test:garage",
            "--observed-at", datetime.now(timezone.utc).isoformat(),
            "--expires-at", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        self.assertEqual((code, error), (0, ""))
        candidate = json.loads(output)["candidate_ids"][0]
        code, _, error = self.invoke("review", "--candidate", candidate, "--status", "accepted", "--revision", "1",
            "--actor", "test", "--reason", "synthetic fixture review")
        self.assertEqual((code, error), (0, ""))
        code, output, error = self.invoke("export")
        self.assertEqual((code, error), (0, ""))
        records = [json.loads(line) for line in output.splitlines()]
        row = next(x for x in records if x["record_type"] == "discovery_handoff_candidate")
        self.assertEqual(row["candidate"]["decision"]["status"], "accepted")
        self.assertFalse(records[0]["acquire_inventory"])
        self.assertTrue(row["evidence"])

    def test_network_requires_explicit_policy_file(self):
        with self.assertRaises(SystemExit) as result:
            self.invoke("run")
        self.assertEqual(result.exception.code, 2)

    def test_replay_requires_original_observation_and_retention_times(self):
        with self.assertRaises(SystemExit) as result:
            self.invoke("inspect-file", "--file", "old.html", "--origin", "https://garage.example/",
                        "--content-type", "text/html", "--policy-ref", "p", "--evidence-group", "g")
        self.assertEqual(result.exception.code, 2)

    def test_doctor_fails_nonzero_on_foreign_key_violation(self):
        import sqlite3
        from discovery.store import Store
        with Store(self.db):
            pass
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO evidence VALUES ('e','k','missing','2026-09-07T00:00:00Z','2099-01-01T00:00:00Z','{}')")
        db.close()
        code, output, error = self.invoke("doctor")
        self.assertNotEqual(code, 0)
        self.assertTrue(json.loads(output)["foreign_key_errors"])

    def test_replay_rejects_empty_expiry_without_creating_evidence(self):
        from discovery.store import Store
        source = self.path / "dealer.html"
        source.write_text('<a href="/car/1">1</a>', encoding="utf-8")
        code, output, error = self.invoke(
            "inspect-file", "--file", str(source), "--origin", "https://garage.example/",
            "--content-type", "text/html", "--policy-ref", "test:synthetic",
            "--evidence-group", "test:garage", "--observed-at",
            datetime.now(timezone.utc).isoformat(), "--expires-at", "")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(json.loads(error)["error"], "ValueError")
        with Store(self.db) as store:
            self.assertEqual(store.count_candidates(), 0)


if __name__ == "__main__":
    unittest.main()
