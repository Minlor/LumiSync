"""Linux AppImage in-place updates: detection, trust rule, atomic swap, restart."""

import base64
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lumisync import updates
from lumisync.updates import (
    UNVERIFIED_UPDATE_MESSAGE,
    ReleaseAsset,
    UpdateCheckResult,
    UpdateVerificationError,
    appimage_child_environment,
    appimage_restart_command,
    appimage_update_target,
    cleanup_stale_appimage_parts,
    install_appimage_update,
    select_checksum_assets,
    select_update_asset,
    signed_message,
)

OLD_IMAGE = b"old appimage, still runnable"
NEW_IMAGE = b"new appimage bytes"
NEW_SHA = hashlib.sha256(NEW_IMAGE).hexdigest()
APPIMAGE_NAME = "LumiSync-x86_64.AppImage"
VERSION = "1.0.1"


def _new_key():
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return private_key, base64.b64encode(public).decode()


def _sign(private_key, version, manifest_bytes) -> bytes:
    return base64.b64encode(private_key.sign(signed_message(version, manifest_bytes))) + b"\n"


def _parts(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.name.endswith(".part"))


class AssetAndManifestSelectionTests(unittest.TestCase):
    def setUp(self):
        self.setup = ReleaseAsset("LumiSync-Setup.exe", "https://example/setup.exe")
        self.appimage = ReleaseAsset(APPIMAGE_NAME, "https://example/app")
        self.tarball = ReleaseAsset(APPIMAGE_NAME + ".tar.gz", "https://example/app.tgz")
        self.windows = ReleaseAsset("LumiSync-Windows-SHA256SUMS.txt", "https://example/win")
        self.linux = ReleaseAsset("LumiSync-Linux-SHA256SUMS.txt", "https://example/linux")
        self.assets = (self.setup, self.windows, self.appimage, self.tarball, self.linux)

    def test_each_platform_picks_its_own_asset(self):
        self.assertIs(select_update_asset(self.assets, platform="linux"), self.appimage)
        self.assertIs(select_update_asset(self.assets, platform="win32"), self.setup)

    def test_each_platform_reads_only_its_own_manifest(self):
        self.assertEqual(select_checksum_assets(self.assets, self.appimage), [self.linux])
        self.assertEqual(select_checksum_assets(self.assets, self.setup), [self.windows])

    def test_a_generic_manifest_and_a_sidecar_still_count(self):
        generic = ReleaseAsset("SHA256SUMS.txt", "https://example/sums")
        sidecar = ReleaseAsset(APPIMAGE_NAME + ".sha256", "https://example/side")
        ordered = select_checksum_assets(
            (self.windows, generic, sidecar, self.linux), self.appimage
        )
        self.assertEqual(ordered, [sidecar, self.linux, generic])


class AppImageDetectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.image = self.directory / APPIMAGE_NAME
        self.image.write_bytes(OLD_IMAGE)

    def target(self, env, **kwargs):
        options = {"platform": "linux", "frozen": True}
        options.update(kwargs)
        return appimage_update_target(env, **options)

    def test_a_writable_appimage_qualifies(self):
        self.assertEqual(self.target({"APPIMAGE": str(self.image)}), Path(os.path.realpath(self.image)))

    def test_missing_or_empty_env_does_not(self):
        self.assertIsNone(self.target({}))
        self.assertIsNone(self.target({"APPIMAGE": "  "}))

    def test_relative_missing_or_non_file_paths_do_not(self):
        self.assertIsNone(self.target({"APPIMAGE": APPIMAGE_NAME}))
        self.assertIsNone(self.target({"APPIMAGE": str(self.directory / "gone.AppImage")}))
        self.assertIsNone(self.target({"APPIMAGE": str(self.directory)}))

    def test_a_read_only_location_does_not(self):
        with patch.object(updates, "_can_replace", return_value=False):
            self.assertIsNone(self.target({"APPIMAGE": str(self.image)}))

    def test_read_only_directory_is_detected_by_the_access_check(self):
        calls = []

        def access(path, mode):
            calls.append((Path(path), mode))
            return Path(path) != self.image.parent.resolve()

        with patch.object(updates.os, "access", access):
            self.assertIsNone(self.target({"APPIMAGE": str(self.image)}))
        self.assertIn(os.W_OK | os.X_OK, [mode for _path, mode in calls])

    def test_source_runs_and_other_platforms_do_not(self):
        env = {"APPIMAGE": str(self.image)}
        self.assertIsNone(self.target(env, frozen=False))
        self.assertIsNone(self.target(env, platform="win32"))
        self.assertIsNone(self.target(env, platform="darwin"))


class AppImageInstallTests(unittest.TestCase):
    def setUp(self):
        self.key, public = _new_key()
        self.keys = (public,)
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.image = self.directory / APPIMAGE_NAME
        self.image.write_bytes(OLD_IMAGE)
        os.chmod(self.image, 0o644)
        self.appimage = ReleaseAsset(APPIMAGE_NAME, "https://example/app")
        self.manifest = ReleaseAsset("LumiSync-Linux-SHA256SUMS.txt", "https://example/linux")
        self.sig = ReleaseAsset("LumiSync-Linux-SHA256SUMS.txt.sig", "https://example/linux.sig")
        self.win_manifest = ReleaseAsset("LumiSync-Windows-SHA256SUMS.txt", "https://example/win")
        self.win_sig = ReleaseAsset("LumiSync-Windows-SHA256SUMS.txt.sig", "https://example/win.sig")
        manifest = f"{NEW_SHA}  {APPIMAGE_NAME}\n{'0' * 64}  {APPIMAGE_NAME}.tar.gz\n".encode()
        self.bodies = {self.manifest.url: manifest, self.sig.url: _sign(self.key, VERSION, manifest)}
        self.image_bytes = NEW_IMAGE
        self.calls: list[str] = []
        for target, replacement in (
            ("lumisync.updates.fetch_asset_bytes", self._fetch),
            ("lumisync.updates.download_update_asset", self._download),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fetch(self, url, max_bytes, timeout=10.0):
        self.calls.append(f"fetch:{url}")
        return self.bodies[url]

    def _download(self, asset, destination, *, progress=None, timeout=30.0):
        self.calls.append(f"download:{asset.url}")
        # The partial file must sit beside the AppImage for an atomic rename.
        self.assertEqual(Path(destination).parent, self.image.parent)
        self.assertTrue(Path(destination).name.startswith("."))
        Path(destination).write_bytes(self.image_bytes)
        if progress is not None:
            progress(len(self.image_bytes), len(self.image_bytes))
        return Path(destination)

    def _result(self, *assets):
        return UpdateCheckResult(
            current_version="1.0.0",
            latest_version=VERSION,
            is_update_available=True,
            assets=assets or (self.appimage, self.manifest, self.sig),
        )

    def _assert_rejected(self, result, error=UpdateVerificationError):
        with self.assertRaises(error) as caught:
            install_appimage_update(result, self.image, public_keys=self.keys)
        self.assertEqual(self.image.read_bytes(), OLD_IMAGE)
        self.assertEqual(_parts(self.directory), [])
        return caught.exception

    def test_signed_release_replaces_the_appimage(self):
        progress = []
        path = install_appimage_update(
            self._result(), self.image, public_keys=self.keys,
            progress=lambda written, total: progress.append((written, total)),
        )
        self.assertEqual(path, self.image)
        self.assertEqual(self.image.read_bytes(), NEW_IMAGE)
        self.assertEqual(_parts(self.directory), [])
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [APPIMAGE_NAME])
        self.assertEqual(progress, [(len(NEW_IMAGE), len(NEW_IMAGE))])
        # The signature is checked before the download starts.
        self.assertEqual(
            self.calls,
            [f"fetch:{self.manifest.url}", f"fetch:{self.sig.url}", f"download:{self.appimage.url}"],
        )
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.image.stat().st_mode), 0o755)

    def test_mode_flush_and_rename_happen_in_order(self):
        steps = []
        real_replace = os.replace
        # 0o755 on Linux; Windows reports every writable file as 0o666.
        expected_mode = updates._executable_mode(self.image.stat().st_mode)
        with patch.object(updates, "_chmod", lambda path, mode: steps.append(("chmod", mode))), \
                patch.object(updates, "_fsync_file", lambda path: steps.append(("fsync",))), \
                patch.object(updates.os, "replace",
                             lambda src, dst: (steps.append(("replace",)), real_replace(src, dst))), \
                patch.object(updates, "_fsync_directory", lambda path: steps.append(("fsync_dir",))):
            install_appimage_update(self._result(), self.image, public_keys=self.keys)
        self.assertEqual(steps, [("chmod", expected_mode), ("fsync",), ("replace",), ("fsync_dir",)])
        if os.name == "posix":
            self.assertEqual(expected_mode, 0o755)

    def test_the_windows_manifest_never_vouches_for_the_appimage(self):
        # Correctly signed, lists the right hash, but it is the other platform's.
        body = f"{NEW_SHA}  {APPIMAGE_NAME}\n".encode()
        self.bodies[self.win_manifest.url] = body
        self.bodies[self.win_sig.url] = _sign(self.key, VERSION, body)
        self._assert_rejected(self._result(self.appimage, self.win_manifest, self.win_sig))
        self.assertEqual(self.calls, [])

    def test_missing_manifest_or_signature_fails_closed(self):
        self._assert_rejected(self._result(self.appimage))
        self._assert_rejected(self._result(self.appimage, self.manifest))
        self.assertFalse(any(call.startswith("download:") for call in self.calls))

    def test_tampered_manifest_fails_closed(self):
        self.bodies[self.manifest.url] = self.bodies[self.manifest.url].replace(b"0", b"1", 1)
        error = self._assert_rejected(self._result())
        self.assertEqual(str(error), UNVERIFIED_UPDATE_MESSAGE)
        self.assertFalse(any(call.startswith("download:") for call in self.calls))

    def test_wrong_key_or_replayed_version_fails_closed(self):
        other, _public = _new_key()
        manifest = self.bodies[self.manifest.url]
        self.bodies[self.sig.url] = _sign(other, VERSION, manifest)
        self._assert_rejected(self._result())
        self.bodies[self.sig.url] = _sign(self.key, "1.0.0", manifest)
        self._assert_rejected(self._result())

    def test_github_digest_alone_is_not_enough_with_pinned_keys(self):
        with_digest = ReleaseAsset(APPIMAGE_NAME, self.appimage.url, digest="sha256:" + NEW_SHA)
        self._assert_rejected(self._result(with_digest))

    def test_hash_mismatch_keeps_the_old_appimage_and_no_partial_file(self):
        self.image_bytes = b"tampered image"
        self._assert_rejected(self._result())
        self.assertIn(f"download:{self.appimage.url}", self.calls)

    def test_a_failed_download_leaves_nothing_behind(self):
        def broken(asset, destination, *, progress=None, timeout=30.0):
            Path(destination).write_bytes(b"half")
            raise OSError("connection reset")

        with patch("lumisync.updates.download_update_asset", broken):
            self._assert_rejected(self._result(), OSError)

    def test_a_failed_rename_or_flush_leaves_the_old_appimage(self):
        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        for name in ("_chmod", "_fsync_file"):
            with self.subTest(step=name), patch.object(updates, name, fail):
                self._assert_rejected(self._result(), OSError)
        with patch.object(updates.os, "replace", fail):
            self._assert_rejected(self._result(), OSError)

    def test_without_pinned_keys_the_release_checksum_is_used(self):
        unsigned = self._result(self.appimage, self.manifest)
        with self.assertLogs("lumisync.updates", level="WARNING"), patch(
            "lumisync.updates.fetch_text", lambda url, timeout=10.0: self.bodies[url].decode()
        ):
            install_appimage_update(unsigned, self.image, public_keys=())
        self.assertEqual(self.image.read_bytes(), NEW_IMAGE)

    def test_a_release_without_an_appimage_is_refused(self):
        self._assert_rejected(self._result(self.manifest, self.sig), RuntimeError)

    def test_stale_partial_files_are_cleaned_up(self):
        stale = self.directory / f".{APPIMAGE_NAME}.update-abc.part"
        fresh = self.directory / f".{APPIMAGE_NAME}.update-def.part"
        unrelated = self.directory / "notes.part"
        for path in (stale, fresh, unrelated):
            path.write_bytes(b"x")
        old = stale.stat().st_mtime - 7200
        os.utime(stale, (old, old))
        self.assertEqual(cleanup_stale_appimage_parts(self.image), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(self.image.exists())

    def test_executable_mode(self):
        self.assertEqual(updates._executable_mode(0o100644), 0o755)
        self.assertEqual(updates._executable_mode(0o100600), 0o700)
        self.assertEqual(updates._executable_mode(0o104755), 0o755)
        self.assertEqual(updates._executable_mode(0o100640), 0o750)


class AppImageRestartTests(unittest.TestCase):
    def test_restart_waits_for_this_process_then_runs_the_appimage_without_arguments(self):
        program, arguments = appimage_restart_command("/home/u/Apps/LumiSync.AppImage", pid=4242)
        self.assertEqual(program, "/bin/sh")
        self.assertEqual(arguments[0], "-c")
        script = arguments[1]
        self.assertIn('kill -0 "$pid"', script)
        self.assertTrue(script.rstrip().endswith('exec "$@"'))
        self.assertEqual(
            arguments[2:], ["lumisync-restart", "4242", str(Path("/home/u/Apps/LumiSync.AppImage"))]
        )
        self.assertNotIn("--minimized", arguments)

    def test_restart_defaults_to_this_pid(self):
        _program, arguments = appimage_restart_command("/x/LumiSync.AppImage")
        self.assertEqual(arguments[3], str(os.getpid()))

    def test_child_environment_drops_the_old_mount_from_library_paths(self):
        env = appimage_child_environment({
            "APPDIR": "/tmp/.mount_Lumi1",
            "LD_LIBRARY_PATH": "/tmp/.mount_Lumi1/usr/bin:/tmp/.mount_Lumi1/usr/bin/_internal:/opt/lib",
            "LD_LIBRARY_PATH_ORIG": "/tmp/.mount_Lumi1/usr/bin",
            "HOME": "/home/u",
        })
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)
        self.assertEqual(env["HOME"], "/home/u")
        # Outside an AppImage nothing changes.
        plain = {"LD_LIBRARY_PATH": "/opt/lib"}
        self.assertEqual(appimage_child_environment(plain), plain)


class LinuxUpdateWiringTests(unittest.TestCase):
    def test_worker_swaps_the_appimage_when_running_from_one(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from lumisync.gui.controllers import update_controller

        result = UpdateCheckResult(current_version="1.0.0", latest_version=VERSION,
                                   is_update_available=True)
        worker = update_controller.UpdateDownloadWorker(result)
        finished, failures = [], []
        worker.finished.connect(finished.append)
        worker.failed.connect(failures.append)
        target = Path("/apps") / APPIMAGE_NAME
        with patch.object(update_controller, "appimage_update_target", return_value=target), \
                patch.object(update_controller, "install_appimage_update",
                             return_value=target) as install, \
                patch.object(update_controller, "download_verified_update") as windows:
            worker.run()
        install.assert_called_once()
        windows.assert_not_called()
        self.assertEqual(finished, [str(target)])
        self.assertEqual(failures, [])

        with patch.object(update_controller, "appimage_update_target", return_value=target), \
                patch.object(update_controller, "install_appimage_update",
                             side_effect=UpdateVerificationError(UNVERIFIED_UPDATE_MESSAGE)):
            worker.run()
        self.assertEqual(failures, [UNVERIFIED_UPDATE_MESSAGE])

    def test_main_window_offers_install_only_for_a_replaceable_appimage(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from lumisync.gui.main_window import LumiSyncMainWindow

        appimage = ReleaseAsset(APPIMAGE_NAME, "https://example/app")
        result = UpdateCheckResult(current_version="1.0.0", latest_version=VERSION,
                                   is_update_available=True, assets=(appimage,))
        no_asset = UpdateCheckResult(current_version="1.0.0", latest_version=VERSION,
                                     is_update_available=True, assets=())
        with patch("lumisync.gui.main_window.sys.platform", "linux"), \
                patch("lumisync.gui.main_window.is_installed_build", return_value=True), \
                patch("lumisync.gui.main_window.appimage_update_target",
                      return_value=Path("/apps") / APPIMAGE_NAME):
            self.assertTrue(LumiSyncMainWindow.can_install_update(None, result))  # type: ignore[arg-type]
            self.assertFalse(LumiSyncMainWindow.can_install_update(None, no_asset))  # type: ignore[arg-type]
        with patch("lumisync.gui.main_window.sys.platform", "linux"), \
                patch("lumisync.gui.main_window.is_installed_build", return_value=True), \
                patch("lumisync.gui.main_window.appimage_update_target", return_value=None):
            self.assertFalse(LumiSyncMainWindow.can_install_update(None, result))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
