"""Measurement parse / scale / weight-conversion (foundation for the serving
scaler, planner, and shopping consolidation)."""
import pytest

from app.services import units


@pytest.mark.parametrize("line,qty,unit,rest", [
    ("2 cups flour", 2.0, "cup", "flour"),
    ("1 1/2 tbsp olive oil", 1.5, "tbsp", "olive oil"),
    ("½ tsp salt", 0.5, "tsp", "salt"),
    ("200 g sugar", 200.0, "g", "sugar"),
    ("1 lb ground beef", 1.0, "lb", "ground beef"),
    ("2-3 cloves garlic", 2.0, None, "cloves garlic"),   # range → low end; clove not a unit
    ("salt to taste", None, None, "salt to taste"),
    ("3 eggs", 3.0, None, "eggs"),
])
def test_parse_line(line, qty, unit, rest):
    p = units.parse_line(line)
    assert p["qty"] == qty
    assert p["unit"] == unit
    assert p["rest"] == rest


@pytest.mark.parametrize("line,factor,expected", [
    ("2 cups flour", 2, "4 cup flour"),
    ("1/2 tsp salt", 3, "1 1/2 tsp salt"),
    ("200 g sugar", 0.5, "100 g sugar"),
    ("salt to taste", 4, "salt to taste"),   # no quantity → unchanged
    ("3 eggs", 2, "6 eggs"),
])
def test_scale_line(line, factor, expected):
    assert units.scale_line(line, factor) == expected


def test_to_grams_weight_and_volume_with_density():
    assert units.to_grams("200 g sugar") == pytest.approx(200)
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53, rel=0.01)
    assert units.to_grams("2 tbsp olive oil") == pytest.approx(14.7868 * 2 * 0.92, rel=0.01)


def test_to_grams_none_without_unit_or_density():
    assert units.to_grams("3 eggs") is None            # no unit
    assert units.to_grams("2 cups diced onion") is None  # no known density


def test_to_weight_line_converts_when_possible():
    assert units.to_weight_line("1 cup flour").endswith("g flour")
    assert units.to_weight_line("2 eggs") == "2 eggs"  # unchanged
