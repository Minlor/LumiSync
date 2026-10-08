import ast
import os
import subprocess
import sys
import tomllib
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from packaging.requirements import Requirement

from lumisync.sync import monitor


ROOT = Path(__file__).resolve().parents[1]


def _requirement(dependencies, name):
    """Return the requirement string for ``name``, or None if it is absent."""
    for dependency in dependencies:
        if Requirement(dependency).name.lower() == name.lower():
            return dependency
    return None


class WindowsPackagingTests(unittest.TestCase):
    def test_bundle_filter_uses_windows_icu_without_changing_linux_libraries(self):
        for spec_name in ("lumisync_onedir.spec", "lumisync_onefile.spec"):
            source = ast.parse((ROOT / "packaging" / "pyinstaller" / spec_name).read_text())
            nodes = [node for node in source.body if
                     (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("_BLOAT_MARKERS", "_WINDOWS_BLOAT_MARKERS") for t in node.targets))
                     or (isinstance(node, ast.If) and ast.unparse(node.test) == "sys.platform == 'win32'")
                     or (isinstance(node, ast.FunctionDef) and node.name == "_is_bundle_bloat")]
            for platform in ("win32", "linux"):
                namespace = {"sys": SimpleNamespace(platform=platform)}
                exec(compile(ast.Module(body=nodes, type_ignores=[]), spec_name, "exec"), namespace)
                excluded = namespace["_is_bundle_bloat"]
                for dll in ("icu.dll", "icuuc.dll", "icuin.dll", "icudt.dll"):
                    with self.subTest(spec=spec_name, platform=platform, dll=dll):
                        self.assertEqual(excluded(("PySide6/" + dll.upper(), "unrelated-build-tool", "BINARY")), platform == "win32")
                # Versioned ICU used by a custom Qt build is a distinct library.
                self.assertFalse(excluded(("icuuc78.dll", "custom-qt", "BINARY")))
                self.assertFalse(excluded(("PySide6/Qt6Core.dll", "qt", "BINARY")))

    def test_gui_startup_check_renders_and_closes_the_main_window(self):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-m", "lumisync", "--check-gui"],
                                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Main window rendered and closed with isolated settings", result.stdout)

    def test_windows_capture_dependencies_include_cv2_provider(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        dependencies = pyproject["project"]["dependencies"]

        # dxcam and its cv2 colour-conversion helper only ship Windows wheels,
        # so both must stay behind a win32 marker or installs break elsewhere.
        for name in ("dxcam", "opencv-python-headless"):
            with self.subTest(dependency=name):
                requirement = _requirement(dependencies, name)
                self.assertIsNotNone(requirement)
                self.assertEqual(
                    str(Requirement(requirement).marker),
                    'sys_platform == "win32"',
                )

        # The non-Windows capture backend is the complement of that marker.
        mss = _requirement(dependencies, "mss")
        self.assertIsNotNone(mss)
        self.assertEqual(
            str(Requirement(mss).marker), 'sys_platform != "win32"'
        )

    def test_pyinstaller_specs_bundle_cv2_lazy_import(self):
        for spec_name in ("lumisync_onedir.spec", "lumisync_onefile.spec"):
            with self.subTest(spec=spec_name):
                spec = (
                    ROOT / "packaging" / "pyinstaller" / spec_name
                ).read_text()

                self.assertIn('safe_collect_dynamic_libs("cv2")', spec)
                self.assertIn('safe_collect_submodules("cv2")', spec)
                self.assertIn('"cv2"', spec)

    def test_pyinstaller_specs_bundle_account_and_tls_dependencies(self):
        for spec_name in ("lumisync_onedir.spec", "lumisync_onefile.spec"):
            spec = (ROOT / "packaging" / "pyinstaller" / spec_name).read_text()
            for package in ("keyring", "qrcode", "tuya_sharing", "paho.mqtt", "certifi", "cryptography"):
                self.assertIn(f'"{package}"', spec)

    def test_dxcam_camera_prefers_numpy_processor_when_available(self):
        class FakeDxcam:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return kwargs

        fake_dxcam = FakeDxcam()

        camera = monitor._create_dxcam_camera(fake_dxcam, output_idx=1)

        self.assertEqual(camera["output_idx"], 1)
        self.assertEqual(camera["processor_backend"], "numpy")
        self.assertEqual(camera["output_color"], "BGRA")

    def test_dxcam_camera_supports_older_create_signature(self):
        class FakeDxcam:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if "processor_backend" in kwargs:
                    raise TypeError("unexpected keyword argument")
                return kwargs

        fake_dxcam = FakeDxcam()

        camera = monitor._create_dxcam_camera(fake_dxcam, output_idx=1)

        self.assertEqual(camera, {"output_idx": 1, "output_color": "BGRA"})
        self.assertEqual(len(fake_dxcam.calls), 2)

    def test_windows_bgra_capture_is_converted_to_rgb_without_cv2(self):
        screen_grab = monitor.ScreenGrab.__new__(monitor.ScreenGrab)
        screen_grab.capture_method = lambda: np.array(
            [[[5, 10, 20, 255]]], dtype=np.uint8
        )

        with patch.object(monitor.GENERAL, "platform", "Windows"):
            frame = screen_grab.capture_array()

        self.assertEqual(frame.tolist(), [[[20, 10, 5]]])

    def test_missing_cv2_raises_clear_capture_error(self):
        screen_grab = monitor.ScreenGrab.__new__(monitor.ScreenGrab)

        def missing_cv2():
            raise ModuleNotFoundError("No module named 'cv2'", name="cv2")

        screen_grab.capture_method = missing_cv2

        with self.assertRaisesRegex(
            monitor.ScreenCaptureDependencyError,
            "opencv-python-headless",
        ):
            screen_grab.capture()


if __name__ == "__main__":
    unittest.main()
