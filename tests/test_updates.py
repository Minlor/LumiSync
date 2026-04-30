import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from lumisync.updates import (
    UpdateCheckResult,
    check_for_update,
    is_newer_version,
    parse_release_payload,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class UpdateCheckTests(unittest.TestCase):
    def test_newer_release_tag_reports_update(self):
        self.assertTrue(is_newer_version("0.4.1", "v0.4.2"))

    def test_same_version_reports_no_update(self):
        self.assertFalse(is_newer_version("0.4.1", "v0.4.1"))

    def test_older_release_reports_no_update(self):
        self.assertFalse(is_newer_version("0.4.1", "v0.4.0"))

    def test_missing_release_tag_returns_controlled_error(self):
        result = parse_release_payload({}, current_version="0.4.1")

        self.assertIsInstance(result, UpdateCheckResult)
        self.assertFalse(result.is_update_available)
        self.assertIn("Invalid release version", result.error)

    def test_parse_release_payload_maps_github_fields(self):
        result = parse_release_payload(
            {
                "tag_name": "v0.4.2",
                "name": "LumiSync 0.4.2",
                "html_url": "https://github.com/Minlor/LumiSync/releases/tag/v0.4.2",
                "published_at": "2026-04-30T12:00:00Z",
            },
            current_version="0.4.1",
        )

        self.assertTrue(result.is_update_available)
        self.assertEqual(result.latest_version, "0.4.2")
        self.assertEqual(result.release_name, "LumiSync 0.4.2")
        self.assertEqual(
            result.release_url,
            "https://github.com/Minlor/LumiSync/releases/tag/v0.4.2",
        )
        self.assertEqual(result.published_at, "2026-04-30T12:00:00Z")
        self.assertIsNone(result.error)

    def test_check_for_update_handles_network_failure(self):
        with patch("lumisync.updates.urlopen", side_effect=URLError("offline")):
            result = check_for_update(current_version="0.4.1")

        self.assertFalse(result.is_update_available)
        self.assertIn("Could not reach GitHub", result.error)

    def test_check_for_update_uses_mocked_github_response(self):
        payload = {
            "tag_name": "v0.4.2",
            "html_url": "https://github.com/Minlor/LumiSync/releases/tag/v0.4.2",
        }

        with patch("lumisync.updates.urlopen", return_value=FakeResponse(payload)):
            result = check_for_update(current_version="0.4.1")

        self.assertTrue(result.is_update_available)
        self.assertEqual(result.latest_version, "0.4.2")


if __name__ == "__main__":
    unittest.main()
