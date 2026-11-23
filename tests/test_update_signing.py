"""Release signing: signed checksum manifests gate in-app update installs."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lumisync import update_keys
from lumisync.updates import (
    MAX_MANIFEST_BYTES,
    MAX_SIGNATURE_BYTES,
    UNVERIFIED_UPDATE_MESSAGE,
    ReleaseAsset,
    UpdateCheckResult,
    UpdateVerificationError,
    download_verified_update,
    fetch_asset_bytes,
    select_checksum_assets,
    select_signature_asset,
    select_update_asset,
    signed_message,
    verify_manifest_signature,
)

ROOT = Path(__file__).resolve().parents[1]


def _select_windows(assets):
    return select_update_asset(assets, platform="win32")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "lumisync_update_signing_tool", ROOT / "tools" / "update_signing.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def _new_key():
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_key, base64.b64encode(public).decode(), base64.b64encode(seed).decode()


def _sign(private_key, version, manifest_bytes) -> bytes:
    return base64.b64encode(private_key.sign(signed_message(version, manifest_bytes))) + b"\n"


class SignatureVerificationTests(unittest.TestCase):
    def setUp(self):
        self.key, self.public, _seed = _new_key()
        self.manifest = b"a" * 64 + b"  LumiSync-Setup.exe\n"
        self.signature = _sign(self.key, "1.0.1", self.manifest)

    def test_signed_message_format(self):
        self.assertEqual(
            signed_message("v1.0.1", b"body"),
            b"LumiSync update manifest v1\n1.0.1\nbody",
        )
        # Normalised like packaging.version: the tag and the app agree.
        self.assertEqual(signed_message("1.0", b"x"), signed_message("v1.0", b"x"))
        self.assertEqual(
            signed_message("1.1.0-rc1", b"x"),
            b"LumiSync update manifest v1\n1.1.0rc1\nx",
        )

    def test_valid_signature_verifies(self):
        self.assertTrue(
            verify_manifest_signature(self.manifest, self.signature, "1.0.1", (self.public,))
        )
        # Accepts the tag spelling and a str signature too.
        self.assertTrue(
            verify_manifest_signature(
                self.manifest, self.signature.decode(), "v1.0.1", (self.public,)
            )
        )

    def test_any_pinned_key_may_verify(self):
        _other, other_public, _ = _new_key()
        self.assertTrue(
            verify_manifest_signature(
                self.manifest, self.signature, "1.0.1", (other_public, self.public)
            )
        )

    def test_wrong_key_fails(self):
        _other, other_public, _ = _new_key()
        self.assertFalse(
            verify_manifest_signature(self.manifest, self.signature, "1.0.1", (other_public,))
        )

    def test_replay_under_a_newer_tag_fails(self):
        self.assertFalse(
            verify_manifest_signature(self.manifest, self.signature, "1.0.2", (self.public,))
        )

    def test_modified_manifest_byte_fails(self):
        tampered = bytearray(self.manifest)
        tampered[0] = ord("b")
        self.assertFalse(
            verify_manifest_signature(bytes(tampered), self.signature, "1.0.1", (self.public,))
        )

    def test_malformed_inputs_fail_without_raising(self):
        cases = [
            (self.manifest, b"not base64 !!", "1.0.1", (self.public,)),
            (self.manifest, self.signature[:40], "1.0.1", (self.public,)),
            (self.manifest, base64.b64encode(b"x" * 63), "1.0.1", (self.public,)),
            (self.manifest, b"\xff\xfe", "1.0.1", (self.public,)),
            (self.manifest, b"", "1.0.1", (self.public,)),
            (self.manifest, self.signature, "1.0.1", ()),
            (self.manifest, self.signature, "1.0.1", ("not-a-key",)),
            (self.manifest, self.signature, "1.0.1", (base64.b64encode(b"k" * 31).decode(),)),
            (self.manifest, self.signature, "not a version", (self.public,)),
            (self.manifest, self.signature, "", (self.public,)),
        ]
        for manifest, signature, version, keys in cases:
            with self.subTest(signature=signature[:12], version=version, keys=keys):
                self.assertFalse(verify_manifest_signature(manifest, signature, version, keys))


class SignatureAssetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.setup = ReleaseAsset("LumiSync-Setup.exe", "https://example/setup.exe")
        self.manifest = ReleaseAsset(
            "LumiSync-Windows-SHA256SUMS.txt", "https://example/sums"
        )
        self.signature = ReleaseAsset(
            "LumiSync-Windows-SHA256SUMS.txt.sig", "https://example/sums.sig"
        )
        self.linux_sig = ReleaseAsset(
            "LumiSync-Linux-SHA256SUMS.txt.sig", "https://example/linux.sig"
        )
        self.sidecar_sig = ReleaseAsset(
            "LumiSync-Setup.exe.sha256.sig", "https://example/sidecar.sig"
        )
        self.assets = (self.setup, self.manifest, self.signature, self.linux_sig, self.sidecar_sig)

    def test_signature_is_found_next_to_its_manifest(self):
        self.assertIs(select_signature_asset(self.manifest, self.assets), self.signature)
        linux = ReleaseAsset("LumiSync-Linux-SHA256SUMS.txt", "https://example/linux")
        self.assertIs(select_signature_asset(linux, self.assets), self.linux_sig)
        other = ReleaseAsset("SHA256SUMS.txt", "https://example/other")
        self.assertIsNone(select_signature_asset(other, self.assets))

    def test_sig_files_are_never_manifests_or_update_assets(self):
        self.assertEqual(select_checksum_assets(self.assets), [self.manifest])
        self.assertEqual(select_checksum_assets(self.assets, self.setup), [self.manifest])
        self.assertIs(select_update_asset(self.assets, platform="win32"), self.setup)
        only_sigs = (self.signature, self.linux_sig, self.sidecar_sig)
        self.assertIsNone(select_update_asset(only_sigs, platform="win32"))
        self.assertIsNone(select_update_asset(only_sigs, platform="linux"))


class _FakeBody:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, size=-1):
        self.requested.append(size)
        return self.stream.read(size)


class SizeCapTests(unittest.TestCase):
    def test_asset_at_the_cap_is_read(self):
        body = _FakeBody(b"x" * MAX_SIGNATURE_BYTES)
        with patch("lumisync.updates.urlopen", return_value=body):
            self.assertEqual(
                len(fetch_asset_bytes("https://example/sig", MAX_SIGNATURE_BYTES)),
                MAX_SIGNATURE_BYTES,
            )
        # Never reads more than one byte past the cap.
        self.assertEqual(body.requested, [MAX_SIGNATURE_BYTES + 1])

    def test_oversized_assets_are_refused(self):
        for cap in (MAX_SIGNATURE_BYTES, MAX_MANIFEST_BYTES):
            with self.subTest(cap=cap):
                with patch(
                    "lumisync.updates.urlopen", return_value=_FakeBody(b"x" * (cap + 50))
                ):
                    with self.assertRaises(ValueError):
                        fetch_asset_bytes("https://example/big", cap)


INSTALLER = b"installer bytes"
INSTALLER_SHA = hashlib.sha256(INSTALLER).hexdigest()


class SignedDownloadFlowTests(unittest.TestCase):
    """``download_verified_update`` with network calls patched out."""

    version = "1.0.1"

    def setUp(self):
        self.key, self.public, _seed = _new_key()
        self.keys = (self.public,)
        self.setup = ReleaseAsset("LumiSync-Setup.exe", "https://example/setup.exe")
        self.manifest = ReleaseAsset("LumiSync-Windows-SHA256SUMS.txt", "https://example/sums")
        self.sig = ReleaseAsset("LumiSync-Windows-SHA256SUMS.txt.sig", "https://example/sums.sig")
        self.bodies = {
            self.manifest.url: f"{INSTALLER_SHA}  LumiSync-Setup.exe\n".encode(),
        }
        self.bodies[self.sig.url] = _sign(self.key, self.version, self.bodies[self.manifest.url])
        self.installer_bytes = INSTALLER
        self.calls: list[str] = []
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        for target, replacement in (
            ("lumisync.updates.fetch_asset_bytes", self._fetch),
            ("lumisync.updates.download_update_asset", self._download),
            ("lumisync.updates.fetch_text", self._fetch_text),
            ("lumisync.updates.select_update_asset", _select_windows),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fetch(self, url, max_bytes, timeout=10.0):
        self.calls.append(f"fetch:{url}")
        data = self.bodies[url]
        if len(data) > max_bytes:
            raise ValueError("too large")
        return data

    def _fetch_text(self, url, timeout=10.0):
        self.calls.append(f"text:{url}")
        return self.bodies[url].decode()

    def _download(self, asset, destination, *, progress=None, timeout=30.0):
        self.calls.append(f"download:{asset.url}")
        Path(destination).write_bytes(self.installer_bytes)
        return Path(destination)

    def _result(self, *assets):
        return UpdateCheckResult(
            current_version="1.0.0",
            latest_version=self.version,
            is_update_available=True,
            assets=assets or (self.setup, self.manifest, self.sig),
        )

    def _assert_rejected(self, result):
        with self.assertRaises(UpdateVerificationError) as caught:
            download_verified_update(result, self.directory, public_keys=self.keys)
        self.assertEqual(str(caught.exception), UNVERIFIED_UPDATE_MESSAGE)
        self.assertEqual(list(self.directory.iterdir()), [])
        return caught.exception

    def test_correctly_signed_release_downloads(self):
        path = download_verified_update(self._result(), self.directory, public_keys=self.keys)
        self.assertEqual(path.read_bytes(), INSTALLER)

    def test_signature_is_checked_before_the_installer_download(self):
        download_verified_update(self._result(), self.directory, public_keys=self.keys)
        self.assertEqual(
            self.calls,
            [
                f"fetch:{self.manifest.url}",
                f"fetch:{self.sig.url}",
                f"download:{self.setup.url}",
            ],
        )

    def test_missing_sig_fails_closed(self):
        self._assert_rejected(self._result(self.setup, self.manifest))
        self.assertFalse(any(call.startswith("download:") for call in self.calls))

    def test_bad_signature_fails_closed(self):
        other, _public, _ = _new_key()
        self.bodies[self.sig.url] = _sign(other, self.version, self.bodies[self.manifest.url])
        self._assert_rejected(self._result())
        self.assertFalse(any(call.startswith("download:") for call in self.calls))

    def test_replayed_old_signature_fails_closed(self):
        self.bodies[self.sig.url] = _sign(self.key, "1.0.0", self.bodies[self.manifest.url])
        self._assert_rejected(self._result())

    def test_installer_not_listed_fails_closed(self):
        self.bodies[self.manifest.url] = f"{INSTALLER_SHA}  Other.exe\n".encode()
        self.bodies[self.sig.url] = _sign(self.key, self.version, self.bodies[self.manifest.url])
        self._assert_rejected(self._result())

    def test_hash_mismatch_fails_closed_and_deletes_the_file(self):
        self.installer_bytes = b"tampered installer"
        self._assert_rejected(self._result())
        self.assertIn(f"download:{self.setup.url}", self.calls)

    def test_api_digest_without_signed_manifest_is_not_accepted(self):
        with_digest = ReleaseAsset(
            self.setup.name, self.setup.url, digest="sha256:" + INSTALLER_SHA
        )
        self._assert_rejected(self._result(with_digest, self.manifest))
        self._assert_rejected(self._result(with_digest))
        self.assertFalse(any(call.startswith("download:") for call in self.calls))

    def test_unsigned_manifest_is_skipped_for_a_signed_one(self):
        unsigned = ReleaseAsset("LumiSync-Setup.exe.sha256", "https://example/sidecar")
        self.bodies[unsigned.url] = b"0" * 64 + b"\n"
        path = download_verified_update(
            self._result(self.setup, unsigned, self.manifest, self.sig),
            self.directory,
            public_keys=self.keys,
        )
        self.assertEqual(path.read_bytes(), INSTALLER)
        self.assertNotIn(f"fetch:{unsigned.url}", self.calls)

    def test_oversized_manifest_is_not_fetched(self):
        big = ReleaseAsset(self.manifest.name, self.manifest.url, size=MAX_MANIFEST_BYTES + 1)
        self._assert_rejected(self._result(self.setup, big, self.sig))
        self.assertEqual(self.calls, [])

    def test_pinned_keys_are_read_at_call_time(self):
        with patch.object(update_keys, "UPDATE_PUBLIC_KEYS", self.keys):
            path = download_verified_update(self._result(), self.directory)
        self.assertEqual(path.read_bytes(), INSTALLER)
        path.unlink()
        _other, other_public, _ = _new_key()
        with patch.object(update_keys, "UPDATE_PUBLIC_KEYS", (other_public,)):
            with self.assertRaises(UpdateVerificationError):
                download_verified_update(self._result(), self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_without_pinned_keys_the_release_checksum_is_used_and_warned(self):
        with_digest = ReleaseAsset(
            self.setup.name, self.setup.url, digest="sha256:" + INSTALLER_SHA
        )
        with self.assertLogs("lumisync.updates", level="WARNING") as logs:
            path = download_verified_update(
                self._result(with_digest), self.directory, public_keys=()
            )
        self.assertEqual(path.read_bytes(), INSTALLER)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("signatures are not being checked", logs.output[0])
        # Unsigned manifests still work as before.
        self.calls.clear()
        unsigned_result = self._result(self.setup, self.manifest)
        with patch.object(update_keys, "UPDATE_PUBLIC_KEYS", ()):
            with self.assertLogs("lumisync.updates", level="WARNING"):
                download_verified_update(unsigned_result, self.directory)
        self.assertEqual(
            self.calls, [f"text:{self.manifest.url}", f"download:{self.setup.url}"]
        )

    def test_without_pinned_keys_a_missing_checksum_still_fails(self):
        with self.assertLogs("lumisync.updates", level="WARNING"):
            with self.assertRaises(RuntimeError) as caught:
                download_verified_update(
                    self._result(self.setup), self.directory, public_keys=()
                )
        self.assertNotIsInstance(caught.exception, UpdateVerificationError)
        self.assertIn("does not publish a SHA-256 checksum", str(caught.exception))

    def test_without_pinned_keys_a_hash_mismatch_still_fails(self):
        self.installer_bytes = b"tampered"
        with self.assertLogs("lumisync.updates", level="WARNING"):
            with self.assertRaises(RuntimeError) as caught:
                download_verified_update(
                    self._result(self.setup, self.manifest), self.directory, public_keys=()
                )
        self.assertIn("failed SHA-256 verification", str(caught.exception))


class DownloadWorkerTests(unittest.TestCase):
    def test_worker_reports_verification_failure_and_removes_its_folder(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from lumisync.gui.controllers import update_controller

        created = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(prefix=None):
            path = real_mkdtemp(prefix=prefix)
            created.append(Path(path))
            return path

        _key, public, _ = _new_key()
        result = UpdateCheckResult(
            current_version="1.0.0",
            latest_version="1.0.1",
            is_update_available=True,
            assets=(ReleaseAsset("LumiSync-Setup.exe", "https://example/setup.exe"),),
        )
        worker = update_controller.UpdateDownloadWorker(result)
        failures, finished = [], []
        worker.failed.connect(failures.append)
        worker.finished.connect(finished.append)
        with patch.object(update_controller.tempfile, "mkdtemp", mkdtemp), patch(
            "lumisync.updates.select_update_asset", _select_windows
        ), patch.object(update_keys, "UPDATE_PUBLIC_KEYS", (public,)):
            worker.run()
        self.assertEqual(failures, [UNVERIFIED_UPDATE_MESSAGE])
        self.assertEqual(finished, [])
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())


class SigningToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.keys_file = self.directory / "update_keys.py"
        shutil.copyfile(ROOT / "lumisync" / "update_keys.py", self.keys_file)

    def _run(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        environ = {k: v for k, v in os.environ.items() if k != tool.SIGNING_KEY_ENV}
        environ.update(env or {})
        with patch.dict(os.environ, environ, clear=True), contextlib.redirect_stdout(
            out
        ), contextlib.redirect_stderr(err):
            code = tool.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_keygen_sign_verify_round_trip(self):
        key_path = self.directory / "signing-key.txt"
        code, out, _err = self._run(
            "keygen",
            "--private-key-out",
            str(key_path),
            "--write-public",
            "--keys-file",
            str(self.keys_file),
        )
        self.assertEqual(code, 0)
        seed = key_path.read_text().strip()
        self.assertEqual(len(base64.b64decode(seed)), 32)
        self.assertNotIn(seed, out)
        self.assertIn("gh secret set UPDATE_SIGNING_KEY <", out)
        pinned = tool.read_pinned_keys(self.keys_file)
        self.assertEqual(len(pinned), 1)
        self.assertIn(pinned[0], out)
        # The real keys module is untouched.
        self.assertEqual(update_keys.UPDATE_PUBLIC_KEYS, ())
        if os.name == "posix":
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)

        manifest = self.directory / "LumiSync-Windows-SHA256SUMS.txt"
        manifest.write_bytes(f"{INSTALLER_SHA}  LumiSync-Setup.exe\n".encode())
        code, out, _err = self._run(
            "sign", "--manifest", str(manifest), "--version", "v1.0.1",
            env={tool.SIGNING_KEY_ENV: seed},
        )
        self.assertEqual(code, 0, _err)
        self.assertNotIn(seed, out)
        sig = manifest.with_name(manifest.name + ".sig")
        sig_text = sig.read_text()
        self.assertTrue(sig_text.endswith("\n"))
        self.assertEqual(len(base64.b64decode(sig_text.strip())), 64)
        # The client accepts it for the same release tag.
        self.assertTrue(
            verify_manifest_signature(manifest.read_bytes(), sig_text, "1.0.1", pinned)
        )

        verify_args = ["verify", "--manifest", str(manifest), "--signature", str(sig),
                       "--keys-file", str(self.keys_file)]
        self.assertEqual(self._run(*verify_args, "--version", "1.0.1")[0], 0)
        self.assertEqual(self._run(*verify_args, "--version", "1.0.2")[0], 1)
        # No pinned key: verification cannot pass.
        self.assertEqual(
            self._run("verify", "--manifest", str(manifest), "--signature", str(sig),
                      "--version", "1.0.1",
                      "--keys-file", str(ROOT / "lumisync" / "update_keys.py"))[0],
            1,
        )

    def test_write_public_keeps_existing_keys(self):
        tool.write_pinned_keys(("existing",), self.keys_file)
        key_path = self.directory / "k"
        code, _out, _err = self._run(
            "keygen", "--private-key-out", str(key_path), "--write-public",
            "--keys-file", str(self.keys_file),
        )
        self.assertEqual(code, 0)
        keys = tool.read_pinned_keys(self.keys_file)
        self.assertEqual(keys[0], "existing")
        self.assertEqual(len(keys), 2)
        # The edited file is still valid Python with the same tuple.
        spec = importlib.util.spec_from_file_location("tmp_keys", self.keys_file)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.UPDATE_PUBLIC_KEYS, keys)

    def test_keygen_refuses_to_overwrite(self):
        key_path = self.directory / "existing-key"
        key_path.write_text("keep me")
        code, _out, err = self._run("keygen", "--private-key-out", str(key_path))
        self.assertEqual(code, 1)
        self.assertIn("overwrite", err)
        self.assertEqual(key_path.read_text(), "keep me")

    def test_keygen_refuses_a_path_inside_the_repository(self):
        key_path = ROOT / "build" / "test-update-signing-key-must-not-exist"
        code, _out, err = self._run("keygen", "--private-key-out", str(key_path))
        self.assertEqual(code, 1)
        self.assertIn("git work tree", err)
        self.assertFalse(key_path.exists())

    def test_sign_refuses_a_missing_or_malformed_key(self):
        manifest = self.directory / "sums.txt"
        manifest.write_text("x")
        code, _out, err = self._run("sign", "--manifest", str(manifest), "--version", "1.0.1")
        self.assertEqual(code, 1)
        self.assertIn(tool.SIGNING_KEY_ENV, err)
        code, _out, err = self._run(
            "sign", "--manifest", str(manifest), "--version", "1.0.1",
            env={tool.SIGNING_KEY_ENV: "not base64!"},
        )
        self.assertEqual(code, 1)
        code, _out, err = self._run(
            "sign", "--manifest", str(manifest), "--version", "1.0.1",
            env={tool.SIGNING_KEY_ENV: base64.b64encode(b"short").decode()},
        )
        self.assertEqual(code, 1)
        self.assertFalse(manifest.with_name("sums.txt.sig").exists())


class ReleaseWorkflowSigningTests(unittest.TestCase):
    def test_windows_release_signs_and_uploads_the_manifest_signature(self):
        workflow = (ROOT / ".github" / "workflows" / "windows-release.yaml").read_text(
            encoding="utf-8"
        )
        sign = workflow.index("tools\\update_signing.py sign")
        verify = workflow.index("tools\\update_signing.py verify")
        attach = workflow.index("Attach artifacts to GitHub Release")
        self.assertLess(workflow.index("Build Windows artifacts"), sign)
        self.assertLess(sign, verify)
        self.assertLess(verify, attach)
        self.assertIn("dist/LumiSync-Windows-SHA256SUMS.txt.sig", workflow[attach:])
        # The secret reaches exactly one step, only as the tool's env var.
        self.assertEqual(workflow.count("secrets."), 1)
        self.assertIn(
            "LUMISYNC_UPDATE_SIGNING_KEY: ${{ secrets.UPDATE_SIGNING_KEY }}", workflow
        )


if __name__ == "__main__":
    unittest.main()
