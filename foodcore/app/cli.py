"""``python3 -m foodcore.cli`` — build and inspect a reference dataset.

Two commands, both run by an operator (or a host app's import job), never in a
request path:

    python3 -m foodcore.cli build-usda --fdc-dir ./FoodData_Central_csv \\
        --out /config/mymeal/foodcore.sqlite --dataset-version "April 2025"

    python3 -m foodcore.cli info --dataset /config/mymeal/foodcore.sqlite

``--dataset-version`` and ``--retrieved`` are **required** for a build because
they become ``Provenance.dataset_version`` / ``retrieved`` on every value the
file yields. A dataset that cannot say which snapshot it is cannot be cited,
and an uncitable value is one this package will not serve.
"""
from __future__ import annotations

import argparse
import datetime
import sys

from .datasets import DatasetError, build_dataset, load_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foodcore.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser(
        "build-usda",
        help="turn a USDA FoodData Central CSV export into a compact dataset file",
    )
    build.add_argument("--fdc-dir", required=True,
                       help="directory containing food.csv, food_portion.csv, …")
    build.add_argument("--out", required=True, help="dataset file to write")
    build.add_argument("--dataset-version", required=True,
                       help='upstream release id, e.g. "April 2025" — it is cited '
                            "in every value's provenance")
    build.add_argument("--retrieved", default=datetime.date.today().isoformat(),
                       help="ISO date this export was downloaded (default: today)")
    build.add_argument("--data-type", action="append", default=[],
                       help="limit to an FDC data_type (repeatable), e.g. "
                            "foundation_food. Default: everything in the export")
    build.add_argument("--limit", type=int, default=None,
                       help="stop after N foods (for a quick trial build)")

    info = sub.add_parser("info", help="describe a dataset file")
    info.add_argument("--dataset", default=None,
                      help="path to the dataset (default: $FOODCORE_DATASET, "
                           "else the bundled minimal one)")

    args = parser.parse_args(argv)
    if args.command == "build-usda":
        return _build_usda(args)
    return _info(args)


def _build_usda(args) -> int:
    # Imported here, not at module scope: the adapter is only needed by a build,
    # and `foodcore.cli info` must work on a host that never imports a parser.
    from .sources.usda import UsdaImportError, load_fdc_csv

    # BOTH halves are inside the try. The parse half was guarded and the
    # STORAGE half was not, so `--out` pointing at a non-existent directory
    # printed a raw `sqlite3.OperationalError` traceback — the exact thing the
    # comment below says this does not do. A ValueError from a malformed row
    # escaped the same way, printing the whole row.
    try:
        export = load_fdc_csv(
            args.fdc_dir,
            version=args.dataset_version,
            retrieved=args.retrieved,
            data_types=tuple(args.data_type) or None,
            limit=args.limit,
        )
        foods, portions, nutrients = export.rows()
        counts = build_dataset(
            args.out,
            sources=[export.source_record()],
            foods=foods,
            portions=portions,
            nutrients=nutrients,
            builder="foodcore.cli build-usda",
        )
    except (UsdaImportError, DatasetError, ValueError) as exc:
        # Typed domain exceptions with curated messages — not a raw traceback,
        # not the csv module's text, and not the sqlite3 driver's.
        print(f"foodcore: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {args.out}")
    print(export.stats.report())
    print("rows: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    # The parser's count and the file's count must agree. When they do not,
    # rows collapsed on a primary key, and the operator is the only one who can
    # decide whether that matters — so say it rather than printing two numbers
    # and hoping somebody compares them.
    expected = {"foods": len(foods), "portions": len(portions),
                "nutrients": len(nutrients)}
    collapsed = {k: (v, counts[k]) for k, v in expected.items() if counts[k] != v}
    if collapsed:
        print("NOTE: rows collapsed on a primary key (parsed -> stored): "
              + ", ".join(f"{k} {a}->{b}" for k, (a, b) in sorted(collapsed.items())),
              file=sys.stderr)
    return 0


def _info(args) -> int:
    try:
        dataset = load_dataset(args.dataset)
    except DatasetError as exc:
        print(f"foodcore: {exc}", file=sys.stderr)
        return 1
    where = "bundled minimal dataset" if dataset.path is None else dataset.path
    print(f"dataset:        {where}")
    print(f"schema version: {dataset.schema_version}")
    if dataset.is_minimal:
        print("NOTE: this is the bundled fallback — definitional facts only, no "
              "USDA or Open Food Facts data. Household measures will mostly "
              "return 'unresolved' until a real dataset is built.")
    for record in dataset.sources:
        print(
            f"source:         {record.provider} {record.dataset_version} "
            f"(retrieved {record.retrieved_at}, {record.license}, "
            f"{record.record_count} records)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
