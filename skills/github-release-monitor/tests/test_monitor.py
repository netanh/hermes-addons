import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "monitor.py"
spec = importlib.util.spec_from_file_location("monitor", SCRIPT)
assert spec and spec.loader
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def tag(name):
    return (name, f"https://example/tags/{name}")


def release(release_id, name):
    return (name, f"https://example/releases/{name}")


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.cfg = {"repos": ["acme/project"], "check_releases": True, "check_tags": True}

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_all_new_tags_and_releases_since_snapshot(self):
        self.state.write_text(json.dumps({"acme/project": {"known_releases": ["1"], "known_tags": ["old"]}}))
        releases = {"1": release("1", "v1"), "2": release("2", "v2"), "3": release("3", "v3")}
        # Deliberately unordered: tag detection must compare the complete set, not assume API order.
        tags = {"old": tag("old"), "v10": tag("v10"), "stable-middle": tag("stable-middle"), "v2": tag("v2")}
        with patch.object(monitor, "list_releases", return_value=releases) as releases_mock, \
             patch.object(monitor, "list_tags", return_value=tags) as tags_mock:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                monitor.check(self.cfg, self.state)
        releases_mock.assert_called_once_with("acme", "project")
        tags_mock.assert_called_once_with("acme", "project")
        lines = out.getvalue().splitlines()
        self.assertEqual(sum("NEW RELEASE:" in line for line in lines), 2)
        self.assertEqual(sum("NEW TAG:" in line for line in lines), 3)
        self.assertTrue(any("stable-middle" in line for line in lines))
        saved = json.loads(self.state.read_text())["acme/project"]
        self.assertEqual(saved["known_tags"], sorted(tags))
        self.assertEqual(saved["known_releases"], sorted(releases))

    def test_tag_listing_paginates_and_does_not_stop_at_latest_page(self):
        first_page = [{"name": f"tag-{i}"} for i in range(monitor.PAGE_SIZE)]
        second_page = [{"name": "stable-middle"}, {"name": "older-tag"}]
        with patch.object(monitor, "gh_api", side_effect=[first_page, second_page]) as api:
            result = monitor.list_tags("acme", "project")
        self.assertEqual(set(result), {item["name"] for item in first_page + second_page})
        self.assertEqual(api.call_count, 2)
        self.assertIn("page=1", api.call_args_list[0].args[0])
        self.assertIn("page=2", api.call_args_list[1].args[0])

    def test_release_listing_paginates_all_published_full_releases(self):
        first_page = [{"id": i, "tag_name": f"v{i}", "draft": False, "prerelease": False} for i in range(monitor.PAGE_SIZE)]
        second_page = [
            {"id": 1000, "tag_name": "stable-middle", "draft": False, "prerelease": False},
            {"id": 1001, "tag_name": "preview", "draft": False, "prerelease": True},
        ]
        with patch.object(monitor, "gh_api", side_effect=[first_page, second_page]) as api:
            result = monitor.list_releases("acme", "project")
        self.assertEqual(len(result), monitor.PAGE_SIZE + 1)
        self.assertIn("1000", result)
        self.assertNotIn("1001", result)
        self.assertEqual(api.call_count, 2)

    def test_partial_error_does_not_block_successful_feed_and_persists(self):
        self.state.write_text(json.dumps({"acme/project": {"known_releases": ["1"], "known_tags": ["v1"]}}))
        tags = {"v1": tag("v1"), "v2": tag("v2")}
        with patch.object(monitor, "list_releases", side_effect=RuntimeError("offline")), \
             patch.object(monitor, "list_tags", return_value=tags):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                monitor.check(self.cfg, self.state)
        saved = json.loads(self.state.read_text())["acme/project"]
        self.assertEqual(saved["known_tags"], ["v1", "v2"])
        self.assertEqual(saved["errors"]["release"]["count"], 1)
        self.assertIn("NEW TAG: v2", out.getvalue())
        self.assertIn("ERROR (release)", out.getvalue())

    def test_tag_url_encodes_tag(self):
        with patch.object(monitor, "gh_api", return_value=[{"name": "v1+preview/rc"},]):
            value = monitor.list_tags("acme", "project")["v1+preview/rc"]
        self.assertEqual(value[0], "v1+preview/rc")
        self.assertTrue(value[1].endswith("v1%2Bpreview%2Frc"))

    def test_failed_later_page_does_not_advance_snapshot(self):
        self.state.write_text(json.dumps({"acme/project": {"known_releases": ["1"], "known_tags": ["old"]}}))
        first_page = [{"name": f"new-{i}"} for i in range(monitor.PAGE_SIZE)]
        with patch.object(monitor, "list_releases", return_value={"1": release("1", "v1")}):
            with patch.object(monitor, "gh_api", side_effect=[first_page, RuntimeError("page 2 unavailable")]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    monitor.check(self.cfg, self.state)
        saved = json.loads(self.state.read_text())["acme/project"]
        self.assertEqual(saved["known_tags"], ["old"])
        self.assertEqual(saved["errors"]["tag"]["count"], 1)
        self.assertIn("ERROR (tag)", out.getvalue())

    def test_config_rejects_invalid_fields_and_repos(self):
        path = Path(self.tmp.name) / "config.json"
        invalid_values = (
            {"repos": []},
            {"repos": ["../oops/x"]},
            {"repos": ["acme/p"], "check_tags": "yes"},
            {"repos": ["acme/p"], "obsolete": 1},
            {"repos": ["acme/p"], "check_tags": False, "check_releases": False},
        )
        for value in invalid_values:
            path.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                monitor.load_config(path)

    def test_init_preserves_existing_snapshots_unless_forced(self):
        self.state.write_text(json.dumps({"acme/project": {"known_releases": ["1"], "known_tags": ["v1"]}, "other/repo": {"known_tags": ["keep"]}}))
        release_data = {"2": release("2", "v2")}
        tag_data = {"v2": tag("v2")}
        with patch.object(monitor, "list_releases", return_value=release_data) as releases_mock:
            with patch.object(monitor, "list_tags", return_value=tag_data) as tags_mock:
                monitor.check(self.cfg, self.state, init_only=True)
        releases_mock.assert_not_called()
        tags_mock.assert_not_called()
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["acme/project"]["known_releases"], ["1"])
        self.assertEqual(saved["other/repo"]["known_tags"], ["keep"])

        with patch.object(monitor, "list_releases", return_value=release_data):
            with patch.object(monitor, "list_tags", return_value=tag_data):
                monitor.check(self.cfg, self.state, init_only=True, force=True)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["acme/project"]["known_releases"], ["2"])
        self.assertEqual(saved["acme/project"]["known_tags"], ["v2"])
        self.assertEqual(saved["other/repo"]["known_tags"], ["keep"])

    def test_check_requires_init_for_missing_snapshot(self):
        with patch.object(monitor, "list_releases", return_value={"1": release("1", "v1")}), \
             patch.object(monitor, "list_tags", return_value={"v1": tag("v1")}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                monitor.check(self.cfg, self.state)
        self.assertIn("MISSING BASELINE (release)", out.getvalue())
        self.assertIn("MISSING BASELINE (tag)", out.getvalue())
        saved = json.loads(self.state.read_text())["acme/project"]
        self.assertNotIn("known_releases", saved)
        self.assertNotIn("known_tags", saved)

    def test_save_uses_atomic_unique_temp(self):
        monitor.save_state(self.state, {"ok": {}})
        self.assertEqual(json.loads(self.state.read_text()), {"ok": {}})
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
