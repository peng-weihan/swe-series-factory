"""Real local Git regression checks; never clone from the network."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from nl2repo_factory.common import FactoryError
from nl2repo_factory.pipeline import acquire_repository


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def git(self, path, *arguments):
        return subprocess.run(["git", "-C", str(path), *arguments], check=True,
                              capture_output=True, text=True).stdout.strip()

    def repository(self):
        source = self.root / "source"
        source.mkdir()
        self.git(source, "init", "-q")
        (source / "project.py").write_text("original = True\n")
        self.git(source, "add", "project.py")
        self.git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-q", "-m", "fixture")
        return source, self.git(source, "rev-parse", "HEAD")

    def test_non_git_snapshot_inside_git_parent_does_not_adopt_parent_head(self):
        source, head = self.repository()
        nested = source / "nested"
        nested.mkdir()
        (nested / "fixture.py").write_text("value = 1\n")
        record = {"url": "https://example.invalid/repo", "local_path": str(nested)}
        observed = acquire_repository(record, source / "new-snapshot")
        self.assertIsNone(observed["commit"])
        with self.assertRaisesRegex(FactoryError, "non-Git"):
            acquire_repository(dict(record, commit=head), self.root / "invalid")
        self.assertEqual(self.git(source, "rev-parse", "HEAD"), head)

    def test_working_tree_snapshot_preserves_dirty_files_without_git_indirection(self):
        source, head = self.repository()
        (source / "project.py").write_text("original = False\n")
        destination = self.root / "snapshot"
        observed = acquire_repository({"url": "https://example.invalid/repo", "local_path": str(source)}, destination)
        self.assertEqual(observed["commit"], head)
        self.assertFalse((destination / ".git").exists())
        self.assertEqual((destination / "project.py").read_text(), "original = False\n")

    def test_locked_commit_gets_independent_clone_and_leaves_dirty_source_unchanged(self):
        source, head = self.repository()
        (source / "project.py").write_text("original = False\n")
        destination = self.root / "locked"
        observed = acquire_repository({"url": "https://example.invalid/repo", "local_path": str(source), "commit": head}, destination)
        self.assertEqual(observed["commit"], head)
        self.assertTrue((destination / ".git").is_dir())
        self.assertEqual((destination / "project.py").read_text(), "original = True\n")
        self.assertEqual((source / "project.py").read_text(), "original = False\n")

    def test_escaping_link_and_recursive_output_rejected(self):
        source, _ = self.repository()
        outside = self.root / "outside.txt"
        outside.write_text("unrelated")
        (source / "link").symlink_to(outside)
        record = {"url": "https://example.invalid/repo", "local_path": str(source)}
        with self.assertRaisesRegex(FactoryError, "escapes"):
            acquire_repository(record, self.root / "snapshot")
        with self.assertRaisesRegex(FactoryError, "outside"):
            acquire_repository(record, source / "nested")


if __name__ == "__main__":
    unittest.main()
