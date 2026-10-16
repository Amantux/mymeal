import dataclasses

import pytest

from foodcore import SourceRecord


def rec(**kw) -> SourceRecord:
    base = dict(
        provider="usda",
        dataset_version="April 2025",
        retrieved_at="2025-04-01",
        license="CC0-1.0",
        record_count=10342,
    )
    base.update(kw)
    return SourceRecord(**base)


def test_a_record_round_trips_through_dicts():
    assert SourceRecord.from_dict(rec().to_dict()) == rec()


def test_from_dict_accepts_snake_case_too():
    # A host app persisting these rows will have serialised them in whichever
    # convention its own API uses.
    data = {
        "provider": "off",
        "dataset_version": "2025-10-01",
        "retrieved_at": "2025-10-02",
        "license": "ODbL-1.0",
        "record_count": 7,
    }
    assert SourceRecord.from_dict(data).dataset_version == "2025-10-01"


def test_it_is_plain_frozen_data_not_an_orm_model():
    # The host owns persistence; an ORM base class here would drag SQLAlchemy
    # into every consumer and break the zero-dependency rule.
    assert dataclasses.is_dataclass(SourceRecord)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec().record_count = 1
    assert not hasattr(SourceRecord, "__tablename__")
    assert not hasattr(SourceRecord, "metadata")


@pytest.mark.parametrize("bad", ["fdc", "USDA", "", None])
def test_provider_must_be_in_the_provenance_vocabulary(bad):
    with pytest.raises(ValueError):
        rec(provider=bad)


@pytest.mark.parametrize("bad", ["MIT", "CC0", "", None])
def test_license_must_be_in_the_provenance_vocabulary(bad):
    with pytest.raises(ValueError):
        rec(license=bad)


def test_dataset_version_and_retrieved_at_are_required():
    with pytest.raises(ValueError):
        rec(dataset_version="  ")
    with pytest.raises(ValueError):
        rec(retrieved_at="")


def test_negative_record_count_is_rejected():
    with pytest.raises(ValueError):
        rec(record_count=-1)


def test_provenance_from_a_snapshot_cannot_disagree_with_it():
    record = rec(provider="off", license="ODbL-1.0", dataset_version="2025-10-01",
                 retrieved_at="2025-10-02")
    prov = record.provenance("3017620422003", "declared", "convert/1", 0.7)
    # The provider, licence and retrieved date are taken FROM the snapshot, so
    # an engine cannot cite CC0 for a value it read out of an ODbL dataset.
    assert (prov.source, prov.license, prov.retrieved) == (
        "off", "ODbL-1.0", "2025-10-02"
    )
