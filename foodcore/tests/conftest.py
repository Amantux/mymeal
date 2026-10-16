import os

import pytest

from foodcore.datasets import build_dataset, load_dataset
from foodcore.sources.usda import load_fdc_csv

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "usda_fdc_sample")

#: The fixture CSVs are real USDA rows but carved out of two exports, so the
#: export's own version/retrieved stamps are stated explicitly here rather than
#: inferred from a directory name that no longer matches upstream's.
FIXTURE_VERSION = "sr_legacy_food/2018-04+foundation_food/2025-04-24"
FIXTURE_RETRIEVED = "2026-10-16"


@pytest.fixture(scope="session")
def export():
    """The PARSE result — what `sources/usda.py` makes of the fixture CSVs.

    Used by the adapter's own tests, which are about reading the published
    schema correctly. Everything else uses `dataset`.
    """
    return load_fdc_csv(FIXTURE_DIR, version=FIXTURE_VERSION,
                        retrieved=FIXTURE_RETRIEVED)


@pytest.fixture(scope="session")
def dataset(export, tmp_path_factory):
    """A real built dataset file, loaded the way a host app loads one.

    The engines are tested through this rather than through the in-memory parse
    result ON PURPOSE: the file is the artifact a host app actually queries, and
    when the two were separate classes the deployable path was the one nothing
    exercised. A parse detail that does not survive the round-trip into SQLite
    — a preparation qualifier, an upstream `amount` — now fails a test here.
    """
    path = tmp_path_factory.mktemp("dataset") / "fixture.sqlite"
    foods, portions, nutrients = export.rows()
    build_dataset(path, sources=[export.source_record()], foods=foods,
                  portions=portions, nutrients=nutrients, builder="tests")
    return load_dataset(str(path))
