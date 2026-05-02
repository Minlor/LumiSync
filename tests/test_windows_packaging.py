import tomllib
import unittest
from pathlib import Path

from lumisync.sync import monitor


ROOT = Path(__file__).resolve().parents[1]


class WindowsPackagingTests(unittest.TestCase):
    def test_windows_capture_dependencies_include_cv2_provider(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        dependencies = pyproject["project"]["dependencies"]

        self.assertIn("dxcam>=0.3.0", dependencies)
        self.assertTrue(
            any(
                dependency.startswith("opencv-python-headless>=")
                and "sys_platform == 'win32'" in dependency
                for dependency in dependencies
            )
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

        self.assertEqual(camera, {"output_idx": 1})
        self.assertEqual(len(fake_dxcam.calls), 2)

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
