"""Shared fixtures for the myMeal integration tests.

Runs under `pytest-homeassistant-custom-component` (a real Home Assistant test
environment). This is NOT part of the backend pytest suite — it needs Home
Assistant (Python 3.13+) and runs in its own CI job.
"""
import os
import sys

import pytest

# Put the repo root on sys.path so `from custom_components.mymeal…` resolves when
# pytest is pointed straight at this nested tests directory (conftest.py → tests →
# mymeal → custom_components → repo root).
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom `mymeal` component in every test."""
    yield
