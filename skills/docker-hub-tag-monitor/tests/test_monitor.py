import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {"images": ["sample/image"], "page_size": 10, "error_log_max_bytes": 120,
                       "exclude_tags": ["latest"], "image_settings": {"sample/image": {"tag_regex": "^v"}}}
        self.tags = [{"name": "latest", "url": "https://hub.docker.com/r/sample/image/tags"}, {"name": "other", "url": "https://hub.docker.com/r/sample/image/tags"}, {"name": "v1", "url": "https://hub.docker.com/r/sample/image/tags"}]

    def tearDown(self): self.tmp.cleanup()

    def test_filter_has_no_fallback(self):
        self.assertIsNone(monitor.select_candidate_tag(self.tags[:2], ["latest"], "^v"))
        self.assertEqual(monitor.select_candidate_tag(self.tags, ["latest"], "^v")["name"], "v1")

    def test_init_is_additive_and_force_only_rebaselines_configured_images(self):
        state = self.root / "state.json"
        out = io.StringIO()
        with patch.object(monitor, "fetch_tags", return_value=self.tags), contextlib.redirect_stdout(out):
            monitor.check(self.config, state, init_only=True)
        payload = json.loads(state.read_text())
        self.assertEqual(payload["sample/image"]["latest_tag"], "v1")
        payload["other/image"] = {"latest_tag": "untouched"}
        state.write_text(json.dumps(payload))

        # Re-running init does not clobber an existing baseline.
        with patch.object(monitor, "fetch_tags", return_value=[{"name": "v2"}]) as fetch, contextlib.redirect_stdout(io.StringIO()):
            monitor.check(self.config, state, init_only=True)
        fetch.assert_not_called()
        self.assertEqual(json.loads(state.read_text())["sample/image"]["latest_tag"], "v1")

        # Adding an image seeds only that image; force only resets configured entries.
        expanded = dict(self.config, images=["sample/image", "sample/second"])
        with patch.object(monitor, "fetch_tags", return_value=[{"name": "v3"}]), contextlib.redirect_stdout(io.StringIO()):
            monitor.check(expanded, state, init_only=True, force=True)
        payload = json.loads(state.read_text())
        self.assertEqual(payload["sample/image"]["latest_tag"], "v3")
        self.assertEqual(payload["sample/second"]["latest_tag"], "v3")
        self.assertEqual(payload["other/image"]["latest_tag"], "untouched")

    def test_missing_baseline_reports_candidate(self):
        out = io.StringIO()
        with patch.object(monitor, "fetch_tags", return_value=self.tags), contextlib.redirect_stdout(out):
            monitor.check(self.config, self.root / "absent.json")
        self.assertIn("NEW TAG: v1", out.getvalue())

    def test_error_streak_persisted_then_cleared(self):
        state = self.root / "state.json"
        with patch.object(monitor, "fetch_tags", side_effect=RuntimeError("offline")), contextlib.redirect_stdout(io.StringIO()):
            monitor.check(self.config, state, error_log=self.root / "errors.log")
        saved = json.loads(state.read_text())["sample/image"]["errors"]
        self.assertEqual(saved["count"], 1)
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        saved["first_error_at"] = old
        payload = json.loads(state.read_text()); payload["sample/image"]["errors"] = saved; state.write_text(json.dumps(payload))
        out = io.StringIO()
        with patch.object(monitor, "fetch_tags", side_effect=RuntimeError("offline")), contextlib.redirect_stdout(out):
            monitor.check(self.config, state, error_log=self.root / "errors.log")
        self.assertIn("PERSISTENT ERROR", out.getvalue())
        with patch.object(monitor, "fetch_tags", return_value=self.tags), contextlib.redirect_stdout(io.StringIO()):
            monitor.check(self.config, state)
        self.assertNotIn("errors", json.loads(state.read_text())["sample/image"])

    def test_log_cap_and_unique_atomic_temp(self):
        log = self.root / "log"
        monitor.append_error_log(log, "sample/image", "x" * 250, 40)
        self.assertLessEqual(log.stat().st_size, 40)
        state = self.root / "nested" / "state.json"
        monitor.save_state(state, {"x": {}})
        self.assertEqual(json.loads(state.read_text()), {"x": {}})
        self.assertEqual(list(state.parent.glob("*.tmp")), [])

    def test_config_validation(self):
        with self.assertRaises(ValueError): monitor.validate_config({"images": ["Bad/Repo"]})
        with self.assertRaises(ValueError): monitor.validate_config({"images": ["a/b"], "page_size": 0})
        with self.assertRaises(ValueError): monitor.validate_config({"images": ["a/b"], "image_settings": {"a/b": {"tag_regex": "["}}})


if __name__ == "__main__": unittest.main()
