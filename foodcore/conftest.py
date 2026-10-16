"""Make ``app`` importable when pytest is run from this directory.

``app`` is a namespace package here (``app/__init__.py`` is owned elsewhere),
and pytest's prepend import mode inserts the *test file's* directory on
sys.path, not the repo root — so without this, ``import app.nutrition`` fails.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
