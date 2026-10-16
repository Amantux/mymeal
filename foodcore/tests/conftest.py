"""Make ``app`` importable when pytest is run from the ``foodcore`` directory.

Deliberately here rather than in a root ``conftest.py`` or a packaging file:
``foodcore/app/__init__.py``, ``pyproject.toml`` and the container packaging are
owned by other work in flight, and a test-only path shim cannot collide with
them.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
