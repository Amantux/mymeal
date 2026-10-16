"""Bundled package data.

A subpackage rather than a bare directory so ``importlib.resources`` can find
the file whether foodcore is installed from a wheel, from a zip, or run from a
source checkout.

What lives here is tiny and definitional — see ``datasets.bundled_dataset``.
The real USDA/Open Food Facts datasets are built on the host and are NEVER
committed.
"""
