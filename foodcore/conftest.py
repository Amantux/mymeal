"""Make ``import foodcore`` work in an un-installed source checkout.

The package directory is named ``app/`` (see pyproject.toml for why it installs
as ``foodcore``), so running ``pytest`` straight from this directory would
otherwise only find it as ``app``. This registers the SAME module object under
the single name ``foodcore``, with its ``__path__`` pointing at ``app/``, so:

* tests import exactly what a host app imports — ``foodcore.x``, never ``app.x``;
* there is ONE copy of each module, so ``isinstance(..., Provenance)`` holds.
  Importing both names would load every module twice and give two
  incompatible ``Provenance`` classes — the kind of bug that wastes an evening.

A real install needs none of this; it is purely a source-checkout convenience.
"""
import importlib.util
import pathlib
import sys

if importlib.util.find_spec("foodcore") is None:
    _pkg_dir = pathlib.Path(__file__).parent / "app"
    _spec = importlib.util.spec_from_file_location(
        "foodcore",
        _pkg_dir / "__init__.py",
        submodule_search_locations=[str(_pkg_dir)],
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["foodcore"] = _module
    _spec.loader.exec_module(_module)
