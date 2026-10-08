from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nl2repo_factory.ast_scan import is_test_asset, is_test_file, scan_repository


class AstScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)

    def write(self, path, text=""):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_two_test_definitions_and_src_layout(self):
        self.assertTrue(is_test_file("pkg/api_test.py"))
        self.assertFalse(is_test_file("testing/helper.py"))
        self.assertTrue(is_test_asset("testing/helper.py"))
        self.assertTrue(is_test_asset("utils/foo_tests_data/helper.py"))
        self.write("src/pkg/__init__.py")
        self.write("src/pkg/api.py", "def public(): return 1\n")
        self.write("tests/test_api.py", "from pkg import public\nfrom src.pkg.api import public\ndef test_ok(): pass\n")
        self.write("testing/helper.py", "def test_hidden(): pass\n")
        result = scan_repository(self.repo)
        self.assertIn("src.pkg", result["project_modules"])
        self.assertNotIn("pkg", result["project_modules"])
        self.assertEqual(result["required_api_symbols"], ["src.pkg.api.public"])
        self.assertEqual(result["test_case_count"], 1)
        self.assertNotIn("testing/helper.py", result["implementation_python_files"])

    def test_public_evidence_imports_and_approximate_count(self):
        for name in ("__init__", "api", "parser", "normalization", "errors"):
            self.write(f"tagkit/{name}.py")
        self.write("tagkit/api.py", '''@decorator
async def normalize(value: str = "") -> list[str]:
    """Keep the public contract."""
    def nested():
        raise RuntimeError("not an outer error")
    if value is None:
        raise ValueError("bad")
    if not value:
        return
    return value.split(",")

class Client(Base):
    def __init__(self): pass
    def _hidden(self): pass
    def run(self, *, flag=True): return flag

def _private(): pass
''')
        self.write("tests/test_api.py", '''from tagkit.api import normalize as other
from tagkit.api import normalize
from tagkit.errors import *
from .tagkit import ignored
import tagkit
import json
@parametrize("x", [1, 2, 3])
def test_data(x): pass
class TestCases:
    def test_one(self): pass
    def helper(self):
        def test_nested(): pass
''')
        self.write(".venv/bogus.py", "invalid !")
        result = scan_repository(self.repo)
        self.assertEqual(result["summary"]["implementation_python_file_count"], 5)
        self.assertEqual(result["required_api_symbols"], ["tagkit", "tagkit.api.normalize", "tagkit.errors.*"])
        self.assertEqual(result["test_case_count"], 2)
        self.assertEqual(len(result["test_imported_repo_symbols"]), 3)
        function = next(item for item in result["public_functions"] if item["name"] == "normalize")
        self.assertTrue(function["is_async"])
        self.assertEqual(function["return_annotation"], "list[str]")
        self.assertTrue(function["has_bare_return"])
        self.assertTrue(function["has_value_return"])
        self.assertEqual(len(function["raises"]), 1)
        client = next(item for item in result["public_classes"] if item["name"] == "Client")
        self.assertEqual([method["name"] for method in client["methods"]], ["run"])
        self.assertTrue(any(item["name"] == "test_data" for item in result["public_functions"]))
        self.assertEqual(result["python_grammar_version"], "3.11")

    def test_syntax_errors_are_recorded_and_python_312_syntax_rejected(self):
        self.write("broken.py", "def invalid(\n")
        self.write("newsyntax.py", "type Alias = int\n")
        result = scan_repository(self.repo)
        self.assertEqual(len(result["parse_errors"]), 2)
        self.assertEqual(result["summary"]["implementation_python_file_count"], 2)

    def test_symlink_assets_are_not_read(self):
        with tempfile.TemporaryDirectory() as external:
            other = Path(external)
            (other / "secret.py").write_text("def must_not_be_seen(): pass\n", encoding="utf-8")
            (self.repo / "link.py").symlink_to(other / "secret.py")
            (self.repo / "outside").symlink_to(other, target_is_directory=True)
            self.write("real.py")
            result = scan_repository(self.repo)
            self.assertEqual(result["python_files"], ["real.py"])
            self.assertEqual({row["path"] for row in result["skipped_paths"]}, {"link.py", "outside"})

    def test_implementation_count_uses_truncated_tree(self):
        self.write("aaa.txt")
        self.write("bbb.txt")
        self.write("zzz.py")
        with patch("nl2repo_factory.ast_scan.TREE_LIMIT", 2):
            result = scan_repository(self.repo)
        self.assertEqual(result["python_files"], ["zzz.py"])
        self.assertEqual(result["implementation_python_files"], [])
        self.assertEqual(result["summary"]["implementation_python_file_count"], 0)
        self.assertTrue(result["implementation_tree"]["truncated"])


if __name__ == "__main__":
    unittest.main()
