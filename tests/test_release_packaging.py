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

    def test_dependency_audit_and_dependabot_are_configured(self):
        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yaml").read_text(encoding="utf-8")

        self.assertIn('package-ecosystem: "pip"', dependabot)
        self.assertIn('package-ecosystem: "github-actions"', dependabot)
        self.assertIn('package-ecosystem: "npm"', dependabot)
        self.assertIn("pip-audit", ci)

    def test_release_docs_and_metadata_match_the_installer_distribution(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("## 1.0.0", changelog)
        self.assertNotIn("Development Status :: 3 - Alpha", pyproject)

        # docs/ holds the maintainer's local notes and is not in git, so a
        # fresh checkout (CI) has no releasing guide to check.
        guide = ROOT / "docs" / "releasing.md"
        if guide.exists():
            releasing = guide.read_text(encoding="utf-8")
            self.assertIn("LumiSync-Setup.exe", releasing)
            self.assertIn("SHA256SUMS.txt", releasing)
            self.assertNotIn("portable ZIP", releasing)
            self.assertNotIn("single-file app", releasing)


if __name__ == "__main__":
    unittest.main()
