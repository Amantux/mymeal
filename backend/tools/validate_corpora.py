#!/usr/bin/env python3
"""Score the held-out corpora, and VALIDATE THE CORPORA THEMSELVES first.

Run:  python3 tools/validate_corpora.py          # score current code
      python3 tools/validate_corpora.py --labels # only audit the labels

Two phases, in this order, because the order is the point:

  PHASE 1 -- audit the labels against an independent reference (USDA). A
  canonical term no reference dataset recognises is a term I invented, and an
  invented expectation fails forever and teaches nobody anything. A corpus
  that has never been audited is an opinion with a test runner attached.

  PHASE 2 -- score the implementation. Reported as four separate numbers,
  never one blended score:

      normalisation   does "Wegman's Avocados" reduce to avocado
      boundaries      does "almond milk" survive intact, AND FOR THE RIGHT REASON
      catalog         does the canonical term exist at all
      conversion      does 1 cup flour weigh 125 g

  Blending them hides the finding that makes this tractable: normalisation and
  catalog coverage are DIFFERENT problems with opposite fixes. Measured on
  main, the normaliser was already correct and the catalog was thin -- so
  "ingredient naming is broken" would have sent someone to rewrite a working
  resolver. Four numbers make that visible in one run.

Exit status is non-zero only on a REGRESSION against baseline.json, not on
absolute coverage -- coverage is meant to climb over several changes, and a
suite that is red until it is perfect gets ignored.
"""
from __future__ import annotations

import json
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
# tests/ has no __init__.py, so pytest puts tests/ ITSELF on sys.path and the
# corpora import as a top-level `fixtures` package. Match that here rather than
# adding tests/__init__.py, so the corpus imports identically from the tool and
# from the suite -- two import paths for one fixture is how they drift.
sys.path.insert(0, os.path.join(_BACKEND, "tests"))

TOL = 0.12
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus_baseline.json")


# --------------------------------------------------------------------------
# Phase 1 -- are the LABELS themselves defensible?
# --------------------------------------------------------------------------
def audit_labels(ds) -> list[str]:
    """Return human-readable complaints about the corpus, not about the code."""
    from fixtures import naming_corpus as nc
    from fixtures import volume_weight_corpus as vw

    problems: list[str] = []

    # Every RESOLVED expectation must match the USDA row it cites. This catches
    # a transcription slip and, more importantly, catches a dataset bump
    # restating a portion -- the corpus then moves deliberately.
    for food, unit, grams, fdc, _note in vw.RESOLVED:
        rec = ds.food_by_source_id(fdc) if hasattr(ds, "food_by_source_id") else None
        if rec is None:
            problems.append(f"RESOLVED[{food}]: fdc {fdc} not in dataset")
            continue
        cups = [p for p in getattr(rec, "portions", []) if p.measure == unit]
        # grams_per_unit, not gram_weight: a row can be "2 cups = 250 g", and
        # comparing the raw gram_weight against a per-cup expectation would
        # make the corpus wrong in exactly the places portions are irregular.
        if not cups:
            problems.append(f"RESOLVED[{food}]: fdc {fdc} has no {unit} portion")
            continue
        if not any(abs(p.grams_per_unit - grams) / grams <= 0.01 for p in cups):
            have = ", ".join(f"{p.grams_per_unit}g" for p in cups)
            problems.append(
                f"RESOLVED[{food}]: expected {grams}g, fdc {fdc} publishes {have}")

    # A canonical term nothing recognises is probably invented. Advisory: USDA
    # describes foods as "Avocados, raw, all commercial varieties", so absence
    # is weak evidence, not proof.
    for term in nc.MUST_BE_KNOWN:
        if not _usda_mentions(ds, term):
            problems.append(f"ADVISORY MUST_BE_KNOWN[{term}]: no USDA food mentions it")

    # The two naming lists must not overlap: a term cannot be required to
    # strip and required to survive.
    strips = {c[0].lower() for c in nc.STRIP}
    keeps = {c[0].lower() for c in nc.PRESERVE}
    for dup in sorted(strips & keeps):
        problems.append(f"CONTRADICTION: {dup!r} is in both STRIP and PRESERVE")

    # Every PRESERVE case must have a reason set that actually encodes the
    # guard. Allowing `unknown` everywhere would make the list vacuous -- it
    # would pass on an empty catalog.
    for raw, _food, reasons, _note in nc.PRESERVE:
        if reasons <= {"unknown"}:
            problems.append(f"VACUOUS PRESERVE[{raw}]: 'unknown' alone proves nothing")

    return problems


def _usda_mentions(ds, term: str) -> bool:
    try:
        return bool(ds.search_foods(term))
    except Exception:
        return True  # cannot check -> do not cry wolf


# --------------------------------------------------------------------------
# Phase 2 -- score the implementation
# --------------------------------------------------------------------------
def score() -> dict:
    from app.services import food_resolve as fr
    from fixtures import naming_corpus as nc
    from fixtures import volume_weight_corpus as vw

    out: dict = {}
    fold = getattr(fr, "fold", lambda s: s.strip().lower())

    # -- normalisation --------------------------------------------------
    rows = []
    for raw, want, reasons, note in nc.STRIP:
        got, qual, why = fr.normalize(raw)
        ok = fold(got) == fold(want)
        rows.append(dict(input=raw, want=want, got=got, reason=why,
                         ok=ok, reason_ok=why in reasons, note=note))
    out["normalisation"] = rows

    # -- boundaries (answer AND reason) ---------------------------------
    rows = []
    for raw, want, reasons, note in nc.PRESERVE:
        got, qual, why = fr.normalize(raw)
        ok = fold(got) == fold(want)
        rows.append(dict(input=raw, want=want, got=got, reason=why,
                         ok=ok, reason_ok=why in reasons, note=note))
    out["boundaries"] = rows

    # -- catalog coverage -----------------------------------------------
    out["catalog"] = [dict(term=t, ok=bool(fr.is_known(t))) for t in nc.MUST_BE_KNOWN]

    # -- conversion, measured at BOTH seams ------------------------------
    #
    # `conversion` is END TO END -- units.to_grams on the line a cook writes,
    # which is what a user actually gets and therefore the headline number.
    # `reference` is the dataset stage alone, which is what carries provenance.
    #
    # Reporting only the reference stage understated coverage badly and, worse,
    # understated it in a MISLEADING direction: "1 cup brown sugar" refuses
    # there (USDA publishes packed 220 and unpacked 145 and no unqualified row,
    # so declining is correct) while the density table answers 212.9 g, which
    # is right. Scored on the reference seam that reads as a failure; scored on
    # what ships, it is a pass. Both are true and they mean different things,
    # so both get a number.
    from app.services import food_reference as ref
    from app.services import units as u

    rows, refrows = [], []
    for food, unit, grams, fdc, note in vw.RESOLVED:
        end = u.to_grams(f"1 {unit} {food}")
        ok = end is not None and abs(end - grams) / grams <= TOL
        rows.append(dict(food=food, unit=unit, want=grams,
                         got=(round(end, 1) if end else None), ok=ok, note=note))

        direct = ref.grams_per(unit, food)
        refrows.append(dict(food=food, unit=unit, want=grams,
                            got=(round(direct, 1) if direct else None),
                            ok=direct is not None
                            and abs(direct - grams) / grams <= TOL, note=note))
    out["conversion"] = rows
    out["reference"] = refrows

    # -- declines: unresolvable and ambiguous must NOT return a number --
    rows = []
    for food, unit, note in vw.UNRESOLVABLE_FROM_DATASET:
        got = ref.grams_per(unit, food)
        rows.append(dict(food=food, got=got, ok=got is None,
                         kind="unresolvable", note=note))
    for food, unit, spread, note in vw.AMBIGUOUS:
        got = ref.grams_per(unit, food)
        # Declining is right. Answering is only defensible if the value is not
        # the with-liquid outlier a naive unqualified-row pick would return.
        rows.append(dict(food=food, got=(round(got, 1) if got else None),
                         ok=got is None, kind="ambiguous",
                         spread=spread, note=note))
    out["declines"] = rows

    # -- weight equivalence ---------------------------------------------
    rows = []
    for variant, base, note in nc.WEIGHT_EQUIVALENT:
        gv, gb = ref.grams_per("cup", variant), ref.grams_per("cup", base)
        # Both unresolved is NOT a pass: "None == None" would make this vacuous
        # on an empty dataset, which is the shape of test that passes with the
        # feature deleted.
        ok = gv is not None and gb is not None and abs(gv - gb) / gb <= 0.02
        rows.append(dict(variant=variant, base=base, got=gv, want=gb, ok=ok, note=note))
    out["equivalence"] = rows

    # -- discrimination between near neighbours -------------------------
    rows = []
    for variant, base, unit, gap, note in vw.DISTINCT_PAIRS:
        # End to end, because a near-neighbour collapse reaches the user
        # through whichever stage answers -- and bread flour's wrong 125 g came
        # from the stage BEHIND the reference lookup. Measuring only the
        # reference seam would have declared that bug fixed while it shipped.
        gv = u.to_grams(f"1 {unit} {variant}")
        gb = u.to_grams(f"1 {unit} {base}")
        if gv is None or gb is None:
            ok, why = False, ("variant unresolved" if gv is None else "base unresolved")
        elif gb == 0:
            ok, why = False, "base is zero"
        else:
            seen = abs(gv - gb) / gb
            # Require most of the published gap, not merely "different". A 1 g
            # difference between two flours is not discrimination, it is noise
            # that would let a rounding change pass for a fix.
            ok = seen >= gap * 0.6
            why = f"gap {seen:.0%}, need >={gap * 0.6:.0%}"
        rows.append(dict(variant=variant, base=base, got=gv, base_got=gb,
                         ok=ok, why=why, note=note))
    out["discrimination"] = rows

    # A conversion that lands inside tolerance but FAILS discrimination is not
    # a success, and must not be counted as one.
    #
    # This is not a nicety. The ratchet counted answers, so swapping a silently
    # wrong 125 g for an honest decline registered as a REGRESSION -- i.e. the
    # metric was paying someone to guess, in an engine whose entire premise is
    # that an unknown is never a number. A gate that rewards invention is worse
    # than no gate. Correctness, not response rate.
    _undiscriminated = {r["variant"] for r in out["discrimination"] if not r["ok"]}
    for row in out["conversion"]:
        if row["food"] in _undiscriminated and row["ok"]:
            row["ok"] = False
            row["note"] = (row["note"] + "  [counted WRONG: inside tolerance but "
                           "indistinguishable from its near neighbour]")
    return out


def summarise(res: dict) -> dict:
    s = {}
    for key in ("normalisation", "boundaries"):
        rows = res[key]
        s[key] = dict(passed=sum(r["ok"] for r in rows), total=len(rows),
                      right_reason=sum(r["ok"] and r["reason_ok"] for r in rows))
    for key in ("catalog", "conversion", "reference", "declines", "equivalence",
                "discrimination"):
        rows = res[key]
        s[key] = dict(passed=sum(r["ok"] for r in rows), total=len(rows))
    return s


def main() -> int:
    from app.services import food_reference as ref

    labels_only = "--labels" in sys.argv
    try:
        ds = ref._dataset()
    except Exception as exc:
        print(f"!! dataset unavailable, label audit skipped: {exc}")
        ds = None

    if ds is not None:
        print("=" * 72)
        print("PHASE 1 -- corpus label audit (independent reference)")
        print("=" * 72)
        problems = audit_labels(ds)
        hard = [p for p in problems if not p.startswith("ADVISORY")]
        for p in problems:
            print(f"  {p}")
        if not problems:
            print("  all labels agree with the reference dataset")
        print(f"\n  {len(hard)} hard problem(s), {len(problems) - len(hard)} advisory")
        if labels_only:
            return 1 if hard else 0

    print("\n" + "=" * 72)
    print("PHASE 2 -- implementation score")
    print("=" * 72)
    res = score()
    s = summarise(res)

    for key in ("normalisation", "boundaries"):
        d = s[key]
        extra = ""
        if d["right_reason"] != d["passed"]:
            extra = f"  <-- {d['passed'] - d['right_reason']} passed for the WRONG REASON"
        print(f"\n{key:14s} {d['passed']}/{d['total']}{extra}")
        for r in res[key]:
            if not r["ok"]:
                print(f"    FAIL {r['input']!r} -> {r['got']!r} (want {r['want']!r},"
                      f" reason={r['reason']}) {r['note']}")
            elif not r["reason_ok"]:
                print(f"    reason {r['input']!r} -> ok but via {r['reason']!r} {r['note']}")

    d = s["catalog"]
    print(f"\n{'catalog':14s} {d['passed']}/{d['total']}")
    miss = [r["term"] for r in res["catalog"] if not r["ok"]]
    if miss:
        print("    unknown: " + ", ".join(miss))

    d = s["conversion"]
    print(f"\n{'conversion':14s} {d['passed']}/{d['total']}  (END TO END -- what a cook gets)")
    for r in res["conversion"]:
        if not r["ok"]:
            print(f"    FAIL {r['food']:18s} want {r['want']:>6}g got {r['got']!r:>8}  {r['note']}")

    d = s["reference"]
    print(f"\n{'reference':14s} {d['passed']}/{d['total']}  (dataset stage alone -- carries provenance)")
    for r in res["reference"]:
        if not r["ok"]:
            print(f"    none {r['food']:18s} want {r['want']:>6}g got {r['got']!r:>8}")

    d = s["declines"]
    print(f"\n{'declines':14s} {d['passed']}/{d['total']}  (must return None)")
    for r in res["declines"]:
        if not r["ok"]:
            print(f"    FAIL {r['food']:18s} returned {r['got']}g; {r['kind']}: {r['note']}")

    d = s["equivalence"]
    print(f"\n{'equivalence':14s} {d['passed']}/{d['total']}  (variant must weigh the base)")
    for r in res["equivalence"]:
        if not r["ok"]:
            print(f"    FAIL {r['variant']:18s} {r['got']!r} vs {r['base']} {r['want']!r}  {r['note']}")

    d = s["discrimination"]
    print(f"\n{'discrimination':14s} {d['passed']}/{d['total']}  (near neighbours must differ)")
    for r in res["discrimination"]:
        if not r["ok"]:
            print(f"    FAIL {r['variant']:16s} {r['got']!r} vs {r['base']} {r['base_got']!r}"
                  f"  [{r['why']}]  {r['note']}")

    # -- regression gate -------------------------------------------------
    print("\n" + "=" * 72)
    if os.path.exists(BASELINE):
        base = json.load(open(BASELINE))
        regressed = []
        for key, d in s.items():
            was = base.get(key, {}).get("passed", 0)
            if d["passed"] < was:
                regressed.append(f"{key}: {was} -> {d['passed']}")
        for key, d in s.items():
            was = base.get(key, {}).get("passed", 0)
            arrow = "=" if d["passed"] == was else ("+" if d["passed"] > was else "-")
            print(f"  {arrow} {key:14s} {was} -> {d['passed']}/{d['total']}")
        if regressed:
            print("\nREGRESSION: " + "; ".join(regressed))
            return 1
        print("\nno regression")
    else:
        json.dump(s, open(BASELINE, "w"), indent=2)
        print(f"  wrote baseline -> {BASELINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
