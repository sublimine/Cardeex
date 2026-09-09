"""Calendar tests exercise persistence, atomic generation and preserved debt."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from discovery.profiles import load_profiles
from discovery.store import Store


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module("discovery.scheduling")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "schedule.sqlite"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        profile = load_profiles()["ES"]
        profile["strategies"] = [profile["strategies"][0]]
        self.spec = dict(schedule_id="fixture", profiles={"ES": profile}, localities=[],
                         starts_at="2026-09-01T00:00:00Z", interval_seconds=86400)

    def test_due_idempotent_restart_and_new_generation_preserve_history(self):
        self.api.register_schedule(self.store, self.spec, actor="test")
        self.assertEqual(self.api.tick(self.store, now="2026-08-31T00:00:00Z")["tasks_generated"], 0)
        first = self.api.tick(self.store, now="2026-09-01T00:00:00Z")
        self.assertEqual(first["tasks_generated"], 1)
        keys = {t["task_key"] for t in self.store.tasks()}
        self.assertEqual(self.api.tick(self.store, now="2026-09-01T00:00:00Z")["tasks_generated"], 0)
        self.store.close()
        self.store = Store(self.path)
        second = self.api.tick(self.store, now="2026-09-02T00:00:00Z")
        self.assertEqual(second["tasks_generated"], 1)
        self.assertEqual(len(self.store.tasks()), 2)
        self.assertTrue(keys.issubset({t["task_key"] for t in self.store.tasks()}))
        self.assertEqual(len(self.api.inspect_schedules(self.path)["generations"]), 2)

    def test_page_resume_and_pause_do_not_erase_backlog(self):
        self.spec["localities"] = [{"country": "ES", "code": "00001", "name": "Fixture"}]
        self.api.register_schedule(self.store, self.spec, actor="test")
        first = self.api.tick(self.store, now="2026-09-03T00:00:00Z", max_tasks=1)
        self.assertEqual(first["tasks_generated"], 1)
        before = self.api.inspect_schedules(self.path)
        self.assertGreater(before["schedules"][0]["offset"], 0)
        self.api.set_paused(self.store, "fixture", True, actor="test", reason="review")
        self.assertEqual(self.api.tick(self.store, now="2026-09-03T00:00:00Z")["tasks_generated"], 0)
        self.api.set_paused(self.store, "fixture", False, actor="test", reason="reviewed")
        self.api.tick(self.store, now="2026-09-03T00:00:00Z", max_tasks=1)
        self.assertEqual(len(self.store.tasks()), 2)
        self.assertEqual(len({t["plan_id"] for t in self.store.tasks()}), 1)

    def test_failure_during_plan_save_rolls_back_generation_and_cursor(self):
        self.api.register_schedule(self.store, self.spec, actor="test")
        original = self.store.save_plan
        def crash(plan):
            original(plan)
            raise RuntimeError("simulated crash before cursor commit")
        with patch.object(self.store, "save_plan", side_effect=crash), self.assertRaises(RuntimeError):
            self.api.tick(self.store, now="2026-09-01T00:00:00Z")
        self.assertEqual(self.store.tasks(), [])
        self.assertEqual(self.api.inspect_schedules(self.path)["generations"], [])
        self.assertEqual(self.api.tick(self.store, now="2026-09-01T00:00:00Z")["tasks_generated"], 1)

    def test_inspection_of_missing_path_never_creates_database(self):
        absent = Path(self.tmp.name) / "absent" / "db.sqlite"
        self.assertEqual(self.api.inspect_schedules(absent)["schedules"], [])
        self.assertFalse(absent.parent.exists())

    def test_configuration_is_immutable_and_budget_is_not_reset(self):
        from discovery.model import Conflict
        self.api.register_schedule(self.store, self.spec, actor="test")
        self.store.reserve_request("assessment:one", "https://fixture.example/", max_requests=5)
        self.api.register_schedule(self.store, self.spec, actor="test")
        with self.assertRaises(Conflict):
            self.api.register_schedule(self.store, dict(self.spec, interval_seconds=1), actor="test")
        self.api.tick(self.store, now="2026-09-01T00:00:00Z")
        self.assertEqual(self.store.budget_status()[0]["spent"], 1)

    def test_invalid_schedule_and_clock_fail_before_work(self):
        for seconds in (True, 0, -1, float("nan"), 999999999):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.api.register_schedule(self.store, dict(self.spec, interval_seconds=seconds), actor="test")
        self.api.register_schedule(self.store, self.spec, actor="test")
        with self.assertRaises(ValueError):
            self.api.tick(self.store, now="2026-09-01")
        with self.assertRaises(ValueError):
            self.api.tick(self.store, now="")
        self.assertEqual(self.store.tasks(), [])

    def test_two_connections_generate_each_window_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.api.register_schedule(self.store, self.spec, actor="test")
        def tick():
            with Store(self.path) as connection:
                return self.api.tick(connection, now="2026-09-01T00:00:00Z")["tasks_generated"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: tick(), range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.store.tasks()), 1)

    def test_persistent_turns_prevent_large_schedule_starving_others(self):
        self.spec["localities"] = [{"country": "ES", "code": "00001", "name": "Fixture"}]
        self.api.register_schedule(self.store, self.spec, actor="test")
        self.api.register_schedule(self.store, dict(self.spec, schedule_id="second"), actor="test")
        first = self.api.tick(self.store, now="2026-09-01T00:00:00Z", max_schedules=1, max_tasks=1)
        second = self.api.tick(self.store, now="2026-09-01T00:00:00Z", max_schedules=1, max_tasks=1)
        self.assertNotEqual(first["pages"][0]["schedule_id"], second["pages"][0]["schedule_id"])


if __name__ == "__main__":
    unittest.main()
