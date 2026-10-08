import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nl2repo_factory.source import load_records, make_task_id, normalize_repo_url


class SourceTests(unittest.TestCase):
    def test_normalization_branch_order(self):
        self.assertEqual(normalize_repo_url(" owner/repo "), "https://github.com/owner/repo")
        self.assertEqual(normalize_repo_url("owner/repo.git"), "https://github.com/owner/repo.git")
        self.assertEqual(normalize_repo_url("git@github.com:owner/repo.git"), "https://github.com/owner/repo")
        self.assertEqual(normalize_repo_url("https://github.com/owner/repo.git"), "https://github.com/owner/repo")
        self.assertEqual(normalize_repo_url("https://example.org/project.git/"), "https://example.org/project.git/")

    def test_identity_hashes_case_sensitive_url(self):
        url = "https://github.com/Example/Repo"
        expected = "github-example-repo__" + hashlib.sha1(url.encode()).hexdigest()[:7]
        self.assertEqual(make_task_id(url), expected)
        self.assertEqual(make_task_id(url + "/"), expected)
        self.assertNotEqual(make_task_id(url.lower()), expected)
        self.assertEqual(make_task_id(url, "__v2--"), expected + "-v2")
        with self.assertRaises(ValueError):
            make_task_id(url, "../escape")

    def test_generic_url_and_empty_fallback(self):
        self.assertTrue(make_task_id("ssh://git.example.org:2222/a/b").startswith("ssh-git-example-org-2222-a-b__"))
        self.assertTrue(make_task_id("").startswith("repo__"))

    def test_selection_filters_then_slices_reverses_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "repos.jsonl"
            records = [{}, {"full_name": "o/a", "commit": "one"},
                       {"html_url": "o/b"}, {"full_name": "o/a", "commit": "two"}, {"clone_url": "o/c"}]
            path.write_text("\n" + "\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
            loaded = load_records(path)
            self.assertEqual([row["input_index"] for row in loaded], [0, 1, 2, 3])
            self.assertEqual(loaded[0]["input_line"], 3)
            self.assertEqual(loaded[0]["task_id"], loaded[2]["task_id"])
            self.assertEqual(loaded[0]["commit"], "one")
            selected = load_records(path, start_index=1, end_index=3, reverse=True, limit=1)
            self.assertEqual([row["input_index"] for row in selected], [2])
            self.assertEqual(len(load_records(path, end_index=999)), 4)

    def test_field_precedence_and_bad_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.jsonl"
            path.write_text(json.dumps({"url": "https://api.github.com/repos/o/a", "full_name": "o/b"}), encoding="utf-8")
            self.assertEqual(load_records(path)[0]["url"], "https://api.github.com/repos/o/a")
            path.write_text("{}\n{bad", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r":2: invalid JSON"):
                load_records(path)
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                load_records(path)


if __name__ == "__main__":
    unittest.main()
