"""The properties that make "standalone library" true rather than aspirational.

These are structural tests. They are cheap, and each one fails loudly the first
time somebody reaches for a host app's convenience.
"""
import ast
import os
import pathlib

import pytest

import foodcore

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

#: Anything in here would make the package non-installable into a host app, or
#: would tie it to one of them.
FORBIDDEN_IMPORTS = {
    "flask", "flask_sqlalchemy", "sqlalchemy", "alembic", "psycopg", "psycopg2",
    "gunicorn", "requests", "httpx", "pydantic", "app",
}


def _module_files():
    return sorted(PKG_DIR.rglob("*.py"))


def _imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import — always fine, and the only way
            # intra-package imports may be written.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_module_imports_nothing_outside_the_standard_library(path):
    forbidden = _imported_roots(path) & FORBIDDEN_IMPORTS

    assert not forbidden, (
        f"{path.name} imports {sorted(forbidden)}. foodcore must install into "
        "two host apps with zero dependencies; an absolute import of a host "
        "app's package ('app') would also break extraction into its own repo."
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_no_path_escapes_the_package(path):
    """Standalone also fails if the CODE reaches out to a host-app path.

    Checked against code, not raw text: an earlier version grepped the whole
    file and failed on a *comment* that explains why foodcore exists by naming
    myMeal's units.py. Referring to a host app in prose is correct and useful;
    opening a file there is the defect. A guard that cannot tell those apart
    trains people to delete the sentence rather than fix the import.
    """
    import ast

    tree = ast.parse(path.read_text())
    # Identify docstrings by NODE, not by value: ast.get_docstring() returns a
    # cleandoc()-normalised string that never equals the raw Constant, so a
    # value-based exclusion silently excludes nothing.
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = next(iter(node.body), None)
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstring_nodes.add(id(first.value))

    code_literals = [n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and id(n) not in docstring_nodes]

    offenders = [v for v in code_literals
                 if "/root/" in v or "backend/app" in v or v.startswith("../")]

    assert not offenders, (
        f"{path.name} builds a path outside the package: {offenders}. "
        "foodcore must install into two host apps and be extractable to its "
        "own repo without edits; the host owns the dataset path and passes it in."
    )


def test_pyproject_declares_zero_runtime_dependencies():
    text = (PKG_DIR.parent / "pyproject.toml").read_text()
    block = text.split("dependencies = ", 1)[1].splitlines()[0]

    assert block.strip() == "[]"


def test_public_surface_is_small_and_explicit():
    # The README is the integration contract; __all__ is its machine-readable
    # half. Growing it is a deliberate act, not a side effect.
    assert set(foodcore.__all__) == {
        "Provenance", "Resolved", "Unresolved", "Outcome", "VocabularyError",
        "SOURCES", "METHODS", "LICENSES",
        "SourceRecord",
        "Dataset", "FoodRow", "FoodRecord", "Portion", "FoodMatch",
        "load_dataset", "build_dataset",
        "__version__", "RULE_EPOCH", "DATASET_SCHEMA_VERSION", "rule_version",
    }


def test_everything_in_all_actually_exists():
    for name in foodcore.__all__:
        assert hasattr(foodcore, name), name


def test_importing_the_package_does_not_require_the_engines():
    # The engines land in sibling modules; `import foodcore` must work before
    # they all exist, and a host that only needs conversions must not pay for
    # the rest. They are exposed lazily via module __getattr__.
    assert "convert" in dir(foodcore)
    with pytest.raises(AttributeError):
        foodcore.not_an_engine


def test_rule_version_derives_from_the_package_epoch():
    assert foodcore.rule_version("convert") == f"convert/{foodcore.RULE_EPOCH}"
    assert foodcore.rule_version("nutrition").startswith("nutrition/")


@pytest.mark.parametrize("bad", ["", "convert/2"])
def test_rule_version_rejects_a_pre_versioned_kind(bad):
    with pytest.raises(ValueError):
        foodcore.rule_version(bad)


def test_the_package_version_is_a_single_source_of_truth():
    text = (PKG_DIR.parent / "pyproject.toml").read_text()

    assert f'version = "{foodcore.__version__}"' in text


def test_the_package_is_imported_as_foodcore_not_app():
    # Importing both names loads every module twice and yields two
    # incompatible Provenance classes. One name, one copy.
    assert foodcore.__name__ == "foodcore"
    assert foodcore.Provenance.__module__ == "foodcore.provenance"


def test_no_service_artefacts_remain():
    # foodcore was briefly built as a Home Assistant add-on. A half-deleted
    # service in the tree is a trap for the next reader.
    root = PKG_DIR.parent
    for stale in ("config.yaml", "Dockerfile", "run.sh", "apparmor.txt",
                  "ha_discovery.py", "alembic.ini", "migrations", "app/api",
                  "app/models", "app/extensions.py", "app/settings.py"):
        assert not os.path.exists(root / stale), stale


def test_every_subpackage_is_declared_for_distribution():
    """MUTATION GUARD for the packaging list.

    A subpackage missing from pyproject's `packages` is invisible in the source
    checkout (the directory is right there) and only explodes on
    `import foodcore.<name>` from an installed wheel. `foodcore.rules` shipped
    exactly that way: 506 tests green, and the installed package could not load
    the allergen rules at all.
    """
    # Parsed with a regex rather than tomllib: that is 3.11+, foodcore supports
    # 3.10, and taking a TOML dependency to read our own manifest would violate
    # the zero-dependency rule this very test file enforces elsewhere.
    import re

    pyproject = (PKG_DIR.parent / "pyproject.toml").read_text()
    block = re.search(r"^packages\s*=\s*\[(.*?)\]", pyproject,
                      re.S | re.M)
    assert block, "pyproject.toml has no `packages = [...]` array"
    declared = set(re.findall(r'"([^"]+)"', block.group(1)))
    on_disk = {
        f"foodcore.{p.name}"
        for p in PKG_DIR.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    } | {"foodcore"}

    assert on_disk <= declared, (
        f"subpackage(s) {sorted(on_disk - declared)} exist on disk but are not "
        "declared in pyproject.toml, so they will be missing from the wheel."
    )
