# Shipped reference dataset

`usda-sr-legacy-2018-04.sqlite.gz` — USDA FoodData Central, **SR Legacy, April
2018 release**, built with `python3 -m foodcore.cli build-usda`.

7,793 foods · 12,323 portion weights · 74,990 nutrient values · 2.3 MB
compressed, 10.4 MB expanded.

## Why this file is committed rather than downloaded at build time

**The release is frozen.** SR Legacy was USDA's final publication of the old
Standard Reference database; April 2018 is the last edition and there will not
be another. A file that never changes has nothing to refresh, which removes the
usual argument against committing a binary.

**A release must not depend on a third party being up.** Downloading at image
build time makes every build — including a hotfix at 3am — contingent on
`fdc.nal.usda.gov` answering. Committing it means the build is deterministic,
reproducible years from now, and works with no network at all.

2.3 MB is the price. It is paid once, by a repository that already carries a
frontend lockfile of comparable size.

## Licence

**CC0 1.0 Universal (public domain).** USDA publishes FoodData Central with no
rights reserved, so redistribution carries no attribution requirement and no
share-alike obligation. It is the one tier in this project's source registry
that can simply be shipped.

Values derived from it carry `license="CC0-1.0"` in their provenance. Where a
value also draws on an Open Food Facts record, the licences COMBINE
(`"CC0-1.0 AND ODbL-1.0"`) — see `app/provenance.py`, because an ODbL
share-alike duty does not evaporate by being mixed with public-domain data.

## Rebuilding, or using a different release

```bash
# any FoodData Central CSV export: SR Legacy, Foundation Foods, Branded
python3 -m foodcore.cli build-usda \
    --fdc-dir ./FoodData_Central_sr_legacy_food_csv_2018-04 \
    --out ./my-dataset.sqlite \
    --dataset-version "SR Legacy 2018-04" \
    --retrieved 2018-04-01
```

Point the host app at the result (`MYMEAL_FOODCORE_DATASET`) to override the
shipped file. The dataset version string is required and is cited in every
value's provenance, so a stored figure can always be traced to the release it
came from.

**Foundation Foods** is the actively-maintained successor and has better
analytical data, but far fewer foods (a few hundred against 7,793) and sparser
portion weights — which is the half this project needs most. Shipping SR Legacy
and letting an operator add Foundation on top is the right default for a recipe
app; the reverse would leave most ingredients unresolvable.
