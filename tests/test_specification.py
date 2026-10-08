import unittest

from nl2repo_factory.specification import clean_start_md, validate_specification


DOCUMENT = '''## Demo Project Architecture
### Project Directory Structure
```text
workspace/
└── pkg/
    ├── __init__.py
    ├── api.py
    ├── parser.py
    ├── normalization.py
    └── errors.py
```
## API Usage Guide
### Core API
The public package exports normalize.
## Detailed Function Implementation Nodes
### Node 1: Parse inputs
Apply the stated parsing rules.
### Node 2: Reject invalid inputs
Reject non-string input.
'''


class SpecificationTests(unittest.TestCase):
    def test_complete_and_missing_api_coverage_with_evidence(self):
        payload = {"required_api_symbols": ["pkg.normalize", "pkg.errors.InvalidInput"],
                   "test_imported_repo_symbols": [{"symbol": "pkg.errors.InvalidInput", "test_path": "tests/test_api.py"}]}
        manifest = {"covered_symbols": ["pkg.normalize", "pkg.errors.InvalidInput"]}
        self.assertTrue(validate_specification(DOCUMENT, manifest, payload)["passed"])
        result = validate_specification(DOCUMENT, {"covered_symbols": ["pkg.normalize"]}, payload)
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_symbols"], ["pkg.errors.InvalidInput"])
        self.assertEqual(result["missing_symbol_sources"][0]["test_path"], "tests/test_api.py")

    def test_short_names_and_full_text_keep_baseline_looseness(self):
        payload = {"required_api_symbols": ["pkg.normalize", "pkg.other.normalize"]}
        self.assertTrue(validate_specification(DOCUMENT, {}, payload)["passed"])
        no_prose = DOCUMENT.replace("exports normalize", "exports a function")
        self.assertTrue(validate_specification(no_prose, {"covered_symbols": ["normalize"]}, payload)["passed"])
        self.assertFalse(validate_specification(no_prose + "\nnormalizer\n", {}, payload)["passed"])
        self.assertTrue(validate_specification(no_prose, {}, {"required_api_symbols": []})["passed"])

    def test_node_numbering_body_and_reported_count(self):
        result = validate_specification(DOCUMENT.replace("Node 2:", "Node 3:"), {}, {})
        self.assertIn("behavior_nodes_not_contiguous", result["errors"])
        result = validate_specification(DOCUMENT, {}, {}, node_count=3)
        self.assertIn("reported_node_count_mismatch", result["errors"])
        result = validate_specification(DOCUMENT.replace("Apply the stated parsing rules.\n", ""), {}, {})
        self.assertIn("behavior_node_empty:1", result["errors"])

    def test_cleaning_removes_whole_test_branch_only_from_tree(self):
        polluted = DOCUMENT.replace("    └── errors.py", "    ├── errors.py\n    └── tests/\n        ├── test_api.py\n        └── fixtures/\n            └── example.json")
        polluted += "\nExamples can mention tests/test_api.py in prose.\n```text\ntests/example.txt\n```\n"
        result = validate_specification(polluted, {}, {})
        self.assertIn("implementation_tree_test_assets", result["errors"])
        clean = clean_start_md(polluted)
        self.assertNotIn("├── test_api.py", clean)
        self.assertNotIn("└── example.json", clean)
        self.assertIn("    └── errors.py", clean)
        self.assertIn("Examples can mention tests/test_api.py in prose.", clean)
        self.assertIn("```text\ntests/example.txt\n```", clean)
        self.assertTrue(validate_specification(clean, {}, {})["passed"])
        self.assertEqual(clean_start_md(clean), clean)

    def test_tree_uses_text_files_and_rejects_traversal(self):
        four_files = DOCUMENT.replace("    ├── parser.py\n", "")
        self.assertIn("implementation_python_file_count_lt_5", validate_specification(four_files, {}, {})["errors"])
        unsafe = DOCUMENT.replace("    ├── parser.py", "    ├── ../parser.py")
        self.assertIn("implementation_tree_unsafe_paths", validate_specification(unsafe, {}, {})["errors"])
        absolute = DOCUMENT.replace("    ├── parser.py", "    ├── /etc/parser.py")
        self.assertIn("implementation_tree_unsafe_paths", validate_specification(absolute, {}, {})["errors"])

    def test_budget_conflict_is_explicit_and_class_methods_group_together(self):
        symbols = [f"pkg.function_{number}" for number in range(61)]
        result = validate_specification(DOCUMENT, {"covered_symbols": symbols, "core_apis": [{"name": symbol} for symbol in symbols]}, {"required_api_symbols": symbols})
        self.assertFalse(result["passed"])
        self.assertTrue(result["api_budget_conflict"])
        result = validate_specification(DOCUMENT, {"covered_symbols": symbols}, {"required_api_symbols": symbols})
        self.assertTrue(result["passed"])
        methods = [f"pkg.Client.method_{number}" for number in range(61)]
        result = validate_specification(DOCUMENT, {"covered_symbols": methods}, {"required_api_symbols": methods, "public_classes": [{"qualified_name": "pkg.Client"}]})
        self.assertTrue(result["passed"])
        self.assertEqual(result["required_core_group_count"], 1)


if __name__ == "__main__":
    unittest.main()
