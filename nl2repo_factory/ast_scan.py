"""Read-only Python AST evidence extraction; no repository code is imported."""

from __future__ import annotations

import ast
import os
import platform
import tokenize
from pathlib import Path, PurePosixPath
from typing import Any


EXCLUDED_COMPONENTS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", "build", "dist", "node_modules",
})
TREE_LIMIT = 5000
EXPRESSION_LIMIT = 200  # Explicit local convention; original cutoff is unspecified.


def is_test_file(path: str | PurePosixPath) -> bool:
    path = PurePosixPath(path)
    return (
        any(part in {"test", "tests"} for part in path.parts[:-1])
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name == "tests.py"
    )


def is_test_asset(path: str | PurePosixPath) -> bool:
    """The intentionally broader exclusion used for implementation trees."""
    for part in PurePosixPath(path).parts:
        part = part.lower()
        if (
            part in {"test", "tests", "testing", "conftest.py", "tests.py"}
            or part.startswith("test_")
            or part.endswith("_test.py")
            or {"test", "tests", "testing"}.intersection(part.replace("_", "-").split("-"))
        ):
            return True
    return False


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _expression(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    text = ast.unparse(node)
    return text if len(text) <= EXPRESSION_LIMIT else text[:EXPRESSION_LIMIT - 3] + "..."


class _Behavior(ast.NodeVisitor):
    """Summarize this function's body without attributing nested bodies to it."""

    def __init__(self) -> None:
        self.bare_return = False
        self.value_return = False
        self.returns: list[str] = []
        self.raises: list[str] = []
        self.calls: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is None:
            self.bare_return = True
        else:
            self.value_return = True
            if len(self.returns) < 12:
                self.returns.append(_expression(node.value) or "")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if len(self.raises) < 30:
            self.raises.append(_expression(node) or "")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if len(self.calls) < 80:
            self.calls.append(_expression(node) or "")
        self.generic_visit(node)


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, module: str, path: str, parent: str = "") -> dict[str, Any]:
    behavior = _Behavior()
    for statement in node.body:
        behavior.visit(statement)
    qualified = ".".join(part for part in (module, parent, node.name) if part)
    signature = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}({ast.unparse(node.args)})"
    if node.returns:
        signature += " -> " + ast.unparse(node.returns)
    return {
        "name": node.name, "qualified_name": qualified, "module": module, "path": path,
        "lineno": node.lineno, "end_lineno": node.end_lineno,
        "signature": signature, "docstring": ast.get_docstring(node),
        "decorators": [ast.unparse(item) for item in node.decorator_list],
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "return_annotation": _expression(node.returns),
        "has_bare_return": behavior.bare_return, "has_value_return": behavior.value_return,
        "return_expressions": behavior.returns, "raises": behavior.raises, "calls": behavior.calls,
    }


def _tree(entries: list[dict[str, Any]]) -> dict[str, Any]:
    visible = entries[:TREE_LIMIT]
    return {"entries": visible, "entry_count": len(visible), "truncated": len(entries) > TREE_LIMIT}


def scan_repository(repo: Path) -> dict[str, Any]:
    """Return the section 6 payload, recording unsafe/unreadable paths.

    Symlinks are skipped (including internal ones), never followed. Parsing uses
    Python 3.11's grammar even if the host interpreter is newer. The caller may
    additionally execute this scanner in the prescribed read-only 3.11 container.
    """
    repo = Path(repo)
    if repo.is_symlink():
        raise ValueError("repository root must not be a symlink")
    repo = repo.resolve(strict=True)
    if not repo.is_dir():
        raise ValueError("repository root must be a directory")
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    def walk_error(error: OSError) -> None:
        skipped.append({"path": str(error.filename), "reason": str(error)})

    for directory, dirs, files in os.walk(repo, followlinks=False, onerror=walk_error):
        base = Path(directory)
        kept_dirs = []
        for name in sorted(dirs):
            path = base / name
            relative = path.relative_to(repo).as_posix()
            if name in EXCLUDED_COMPONENTS:
                continue
            if path.is_symlink():
                skipped.append({"path": relative, "reason": "symlink"})
                continue
            kept_dirs.append(name)
            entries.append({"path": relative, "type": "directory", "bytes": 0})
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(repo).as_posix()
            if name in EXCLUDED_COMPONENTS:
                continue
            if path.is_symlink():
                skipped.append({"path": relative, "reason": "symlink"})
                continue
            try:
                if not path.resolve(strict=True).is_relative_to(repo):
                    skipped.append({"path": relative, "reason": "outside_repository"})
                    continue
                if not path.is_file():
                    skipped.append({"path": relative, "reason": "not_regular_file"})
                    continue
                entries.append({"path": relative, "type": "file", "bytes": path.stat().st_size})
            except OSError as exc:
                skipped.append({"path": relative, "reason": str(exc)})
    entries.sort(key=lambda item: item["path"])
    project_tree = _tree(entries)
    implementation_tree = _tree([item for item in entries if not is_test_asset(item["path"])])
    python_files = [item["path"] for item in entries if item["type"] == "file" and item["path"].endswith(".py")]
    implementation_files = [item["path"] for item in implementation_tree["entries"] if item["type"] == "file" and item["path"].endswith(".py")]
    modules = sorted({module_name(path) for path in python_files} - {""})
    roots = sorted({module.split(".")[0] for module in modules})
    module_set, root_set = set(modules), set(roots)
    public_functions: list[dict[str, Any]] = []
    public_classes: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    seen_imports: set[tuple[str, str, str]] = set()
    parse_errors: list[dict[str, Any]] = []
    test_count = 0

    for path in python_files:
        try:
            with tokenize.open(repo / path) as stream:
                source = stream.read()
            parsed = ast.parse(source, filename=path, feature_version=(3, 11))
        except (SyntaxError, UnicodeError, OSError, LookupError, ValueError) as exc:
            parse_errors.append({"path": path, "error": str(exc), "lineno": getattr(exc, "lineno", None)})
            continue
        module = module_name(path)
        test_file = is_test_file(path)
        for node in parsed.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if test_file and node.name.startswith("test"):
                    test_count += 1
                if not node.name.startswith("_"):
                    public_functions.append(_function(node, module, path))
            elif isinstance(node, ast.ClassDef):
                if test_file:
                    test_count += sum(isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test") for method in node.body)
                if not node.name.startswith("_"):
                    public_classes.append({
                        "name": node.name, "qualified_name": ".".join(part for part in (module, node.name) if part),
                        "module": module, "path": path, "lineno": node.lineno, "end_lineno": node.end_lineno,
                        "bases": [ast.unparse(base) for base in node.bases],
                        "decorators": [ast.unparse(item) for item in node.decorator_list],
                        "docstring": ast.get_docstring(node),
                        "methods": [_function(method, module, path, node.name) for method in node.body
                                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_")],
                    })
        if not test_file:
            continue
        for node in ast.walk(parsed):
            candidates: list[tuple[str, str, str]] = []
            if isinstance(node, ast.Import):
                candidates = [("import", alias.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                candidates = [("from", node.module, f"{node.module}.{alias.name}") for alias in node.names]
            for kind, imported_module, symbol in candidates:
                if imported_module not in module_set and imported_module.split(".")[0] not in root_set:
                    continue
                key = (kind, symbol, path)
                if key in seen_imports:
                    continue
                seen_imports.add(key)
                imports.append({"kind": kind, "module": imported_module, "symbol": symbol,
                                "test_path": path, "lineno": node.lineno, "statement": ast.get_source_segment(source, node) or ast.unparse(node)})
    imports.sort(key=lambda item: (item["kind"], item["symbol"], item["test_path"]))
    summary = {
        "python_file_count": len(python_files), "implementation_python_file_count": len(implementation_files),
        "public_class_count": len(public_classes), "public_function_count": len(public_functions),
        "test_case_count": test_count, "parse_error_count": len(parse_errors),
        "required_api_symbol_count": len({item["symbol"] for item in imports}),
        "skipped_path_count": len(skipped),
    }
    return {
        "schema_version": "teamfactory.stage2_ast_payload.v1", "repo_root": str(repo),
        "scanner_python_version": platform.python_version(), "python_grammar_version": "3.11",
        "project_import_roots": roots, "project_modules": modules,
        "project_tree": project_tree, "implementation_tree": implementation_tree,
        "python_files": python_files, "implementation_python_files": implementation_files,
        "public_classes": public_classes, "public_functions": public_functions,
        "test_imported_repo_symbols": imports,
        "required_api_symbols": sorted({item["symbol"] for item in imports}),
        "test_case_count": test_count, "parse_errors": parse_errors,
        "skipped_paths": skipped, "summary": summary,
    }
