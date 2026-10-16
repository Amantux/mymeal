import os

import pytest

from foodcore.sources.usda import load_fdc_csv

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "usda_fdc_sample")

#: The fixture CSVs are real USDA rows but carved out of two exports, so the
#: export's own version/retrieved stamps are stated explicitly here rather than
#: inferred from a directory name that no longer matches upstream's.
FIXTURE_VERSION = "sr_legacy_food/2018-04+foundation_food/2025-04-24"
FIXTURE_RETRIEVED = "2026-10-16"


@pytest.fixture(scope="session")
def dataset():
    return load_fdc_csv(FIXTURE_DIR, version=FIXTURE_VERSION,
                        retrieved=FIXTURE_RETRIEVED)
