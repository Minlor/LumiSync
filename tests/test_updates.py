import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from lumisync.updates import (
    ReleaseAsset,
    UpdateCheckResult,
    asset_sha256,
    check_for_update,
    download_update_asset,
    expected_sha256,
    is_newer_version,
    parse_checksums,
    parse_release_payload,
    select_checksum_asset,
    select_checksum_assets,
    select_update_asset,
    verify_file_sha256,
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
                "body": "## Changes\n- Fixed a thing.",
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
        self.assertEqual(result.body, "## Changes\n- Fixed a thing.")
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

    def test_parse_release_payload_keeps_asset_metadata(self):
        result = parse_release_payload(
            {
                "tag_name": "v1.0.0",
                "assets": [
                    {
                        "name": "LumiSync-1.0.0-Setup.exe",
                        "browser_download_url": "https://example/setup.exe",
                        "size": 1234,
                        "digest": "sha256:" + "a" * 64,
                    },
                    "not an asset",
                ],
            },
            current_version="0.9.0",
        )

        self.assertEqual(len(result.assets), 1)
        self.assertEqual(result.assets[0].name, "LumiSync-1.0.0-Setup.exe")
        self.assertEqual(result.assets[0].size, 1234)
        self.assertEqual(result.assets[0].digest, "sha256:" + "a" * 64)

    def test_windows_asset_selection_prefers_the_installer(self):
        assets = (
            ReleaseAsset("LumiSync.exe", "https://example/portable.exe"),
            ReleaseAsset("LumiSync-1.0.0-Setup.exe", "https://example/setup.exe"),
            ReleaseAsset("SHA256SUMS.txt", "https://example/sums"),
        )

        selected = select_update_asset(assets, platform="win32")

        self.assertEqual(selected.name, "LumiSync-1.0.0-Setup.exe")

    def test_linux_asset_selection_uses_the_appimage(self):
        assets = (
            ReleaseAsset("LumiSync.exe", "https://example/portable.exe"),
            ReleaseAsset("LumiSync-x86_64.AppImage", "https://example/appimage"),
        )

        selected = select_update_asset(assets, platform="linux")

        self.assertEqual(selected.name, "LumiSync-x86_64.AppImage")

    def test_checksum_manifest_parsing_and_verification(self):
        data = b"installer bytes"
        digest = hashlib.sha256(data).hexdigest()
        assets = (
            ReleaseAsset(
                "LumiSync-1.0.0-Setup.exe",
                "https://example/setup.exe",
            ),
            ReleaseAsset("SHA256SUMS.txt", "https://example/sums"),
        )

        checksums = parse_checksums(f"{digest}  LumiSync-1.0.0-Setup.exe\n")
        self.assertEqual(
            checksums["lumisync-1.0.0-setup.exe"],
            digest,
        )
        self.assertIs(select_checksum_asset(assets), assets[1])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installer.exe"
            path.write_bytes(data)
            self.assertTrue(verify_file_sha256(path, digest))
            self.assertFalse(verify_file_sha256(path, "0" * 64))

    def test_asset_digest_prefers_github_sha256_prefix(self):
        asset = ReleaseAsset(
            "LumiSync-1.0.0-Setup.exe",
            "https://example/setup.exe",
            digest="sha256:" + "b" * 64,
        )

        self.assertEqual(asset_sha256(asset), "b" * 64)

    def test_download_update_asset_streams_and_reports_progress(self):
        chunks = [b"abc", b"def", b""]
        progress = []

        class FakeBinaryResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, _size=-1):
                return chunks.pop(0)

        asset = ReleaseAsset(
            "LumiSync-1.0.0-Setup.exe",
            "https://example/setup.exe",
            size=6,
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "setup.exe"
            with patch(
                "lumisync.updates.urlopen",
                return_value=FakeBinaryResponse(),
            ):
                result = download_update_asset(
                    asset,
                    destination,
                    progress=lambda written, total: progress.append((written, total)),
                )

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b"abcdef")
            self.assertEqual(progress, [(3, 6), (6, 6)])


class ChecksumLookupTests(unittest.TestCase):
    """Windows and Linux attach separate manifests to the same release."""

    def setUp(self):
        self.setup = ReleaseAsset("LumiSync-Setup.exe", "https://example/setup.exe")
        self.linux = ReleaseAsset("LumiSync-Linux-SHA256SUMS.txt", "https://example/linux")
        self.windows = ReleaseAsset("LumiSync-Windows-SHA256SUMS.txt", "https://example/windows")
        self.manifests = {
            self.linux.url: "a" * 64 + "  LumiSync-x86_64.AppImage\n",
            self.windows.url: "b" * 64 + "  LumiSync-Setup.exe\n",
        }

    def test_platform_manifest_is_tried_first(self):
        assets = (self.setup, self.linux, self.windows)
        ordered = select_checksum_assets(assets, self.setup)
        self.assertEqual(ordered[0], self.windows)

    def test_lookup_reads_each_manifest_until_one_lists_the_file(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return self.manifests[url]

        # Even with the Linux manifest listed first, the installer's hash is found.
        assets = (self.setup, self.linux, self.windows)
        self.assertEqual(expected_sha256(self.setup, assets, fetch=fetch), "b" * 64)
        self.assertEqual(fetched, [self.windows.url])

    def test_lookup_skips_a_manifest_that_does_not_list_the_file(self):
        assets = (self.setup, self.linux)
        self.assertIsNone(
            expected_sha256(self.setup, assets, fetch=lambda url: self.manifests[url])
        )

    def test_github_digest_wins_without_fetching(self):
        asset = ReleaseAsset(
            "LumiSync-Setup.exe", "https://example/setup.exe", digest="sha256:" + "c" * 64
        )

        def fail(_url):
            raise AssertionError("no manifest fetch expected")

        self.assertEqual(expected_sha256(asset, (asset, self.windows), fetch=fail), "c" * 64)

    def test_unreachable_manifest_is_skipped(self):
        def fetch(url):
            if url == self.windows.url:
                raise OSError("offline")
            return self.manifests[url]

        self.assertIsNone(expected_sha256(self.setup, (self.setup, self.windows), fetch=fetch))


if __name__ == "__main__":
    unittest.main()
