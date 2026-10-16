"""Make ``import foodcore`` work in an un-installed source checkout.

The package directory is named ``app/`` (see pyproject.toml for why it installs
as ``foodcore``), so running ``pytest`` straight from this directory would
otherwise only find it as ``app``. This registers the SAME module object under
the single name ``foodcore``, with its ``__path__`` pointing at ``app/``, so:

* tests import exactly what a host app imports — ``foodcore.x``, never ``app.x``;
* there is ONE copy of each module, so ``isinstance(..., Provenance)`` holds.
  Importing both names would load every module twice and give two
  incompatible ``Provenance`` classes — the kind of bug that wastes an evening.

This registration is **unconditional**, and deliberately overrides an installed
``foodcore`` if one is present. It used to defer to the installed copy, which
meant that on a machine where a host app had ``pip install``ed foodcore, running
``pytest`` in this checkout silently tested the INSTALLED package — green on
code that was not the code being edited, and red with mystery ImportErrors the
moment a new name was added here. Running the tests in a checkout means testing
the checkout. The installed artifact is verified separately, by the clean-venv
install test in the README.
"""
import importlib.util
import pathlib
import sys

_pkg_dir = pathlib.Path(__file__).parent / "app"
_spec = importlib.util.spec_from_file_location(
    "foodcore",
    _pkg_dir / "__init__.py",
    submodule_search_locations=[str(_pkg_dir)],
)
_module = importlib.util.module_from_spec(_spec)
# Drop any already-registered copy (an installed wheel, or a stale partial
# import) so there is exactly ONE `foodcore` in sys.modules: two would give two
# incompatible `Provenance` classes.
for _name in [n for n in sys.modules if n == "foodcore" or n.startswith("foodcore.")]:
    del sys.modules[_name]
sys.modules["foodcore"] = _module
_spec.loader.exec_module(_module)
