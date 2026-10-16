"""Make `app.*` importable when pytest is run from the foodcore directory.

`app/` has no `__init__.py` on purpose (another agent owns that file), so it is
a PEP 420 namespace package and only needs this directory on `sys.path`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
