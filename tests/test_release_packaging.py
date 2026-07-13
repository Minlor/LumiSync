import re
import unittest
from pathlib import Path

from packaging.version import Version

from lumisync import __version__


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_package_version_is_valid_release_version(self):
        self.assertEqual(str(Version(__version__)), __version__)

    def test_pypi_workflow_checks_release_version_before_upload(self):
        workflow = (ROOT / ".github" / "workflows" / "pypi.yaml").read_text()

        self.assertLess(
            workflow.index("Validate release version"),
            workflow.index("Upload distributions"),
        )
        self.assertIn("Release version mismatch", workflow)

    def test_pypi_publish_skips_existing_files_on_rerun(self):
        workflow = (ROOT / ".github" / "workflows" / "pypi.yaml").read_text()

        self.assertRegex(workflow, re.compile(r"skip-existing:\s*true"))


if __name__ == "__main__":
    unittest.main()
