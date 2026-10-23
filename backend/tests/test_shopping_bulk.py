"""Tests for shopping list bulk actions: clear-checked and bulk-items endpoints."""


def test_clear_checked_removes_only_checked_items(auth_client, app, gid):
    """Clear-checked should delete only checked items, leaving unchecked ones."""
    # Create a shopping list
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Test List"})
    sl_id = sl_response.get_json()["id"]

    # Add some items
    auth_client.post(f"/api/v1/shopping-lists/{sl_id}/items", json={"display": "Milk"})
    auth_client.post(f"/api/v1/shopping-lists/{sl_id}/items", json={"display": "Eggs"})
    auth_client.post(f"/api/v1/shopping-lists/{sl_id}/items", json={"display": "Bread"})

    # Check the middle item
    items = auth_client.get(f"/api/v1/shopping-lists/{sl_id}").get_json()["items"]
    eggs_item = next(i for i in items if i["display"] == "Eggs")
    auth_client.put(f"/api/v1/shopping-lists/items/{eggs_item['id']}", json={"checked": True})

    # Clear checked items
    response = auth_client.post(f"/api/v1/shopping-lists/{sl_id}/clear-checked")
    assert response.status_code == 200
    result = response.get_json()

    # Verify only unchecked items remain
    item_displays = [i["display"] for i in result["items"]]
    assert "Eggs" not in item_displays, "Checked item should be removed"
    assert "Milk" in item_displays, "Unchecked item should remain"
    assert "Bread" in item_displays, "Unchecked item should remain"


def test_clear_checked_on_empty_list(auth_client, app, gid):
    """Clear-checked on a list with no items should succeed."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Empty List"})
    sl_id = sl_response.get_json()["id"]

    response = auth_client.post(f"/api/v1/shopping-lists/{sl_id}/clear-checked")
    assert response.status_code == 200
    result = response.get_json()
    assert result["items"] == []


def test_clear_checked_on_all_checked(auth_client, app, gid):
    """Clear-checked when all items are checked should leave an empty list."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "All Checked"})
    sl_id = sl_response.get_json()["id"]

    # Add and check all items
    for display in ["Milk", "Eggs", "Bread"]:
        resp = auth_client.post(f"/api/v1/shopping-lists/{sl_id}/items", json={"display": display})
        item_id = resp.get_json()["id"]
        auth_client.put(f"/api/v1/shopping-lists/items/{item_id}", json={"checked": True})

    response = auth_client.post(f"/api/v1/shopping-lists/{sl_id}/clear-checked")
    assert response.status_code == 200
    result = response.get_json()
    assert result["items"] == []


def test_bulk_items_creates_multiple_items(auth_client, app, gid):
    """Bulk-items should create one item per non-blank line."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Bulk Test"})
    sl_id = sl_response.get_json()["id"]

    lines = ["2 cups flour", "1 gal milk", "salt", "3 cloves garlic"]
    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": lines}
    )
    assert response.status_code == 201
    result = response.get_json()

    assert result["added"] == 4
    assert len(result["items"]) == 4

    displays = [i["display"] for i in result["items"]]
    assert "flour" in displays
    assert "milk" in displays
    assert "salt" in displays
    assert "garlic" in displays


def test_bulk_items_parses_quantity_and_unit(auth_client, app, gid):
    """Bulk-items should parse quantity and unit from lines."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Parse Test"})
    sl_id = sl_response.get_json()["id"]

    lines = ["2 cups flour", "1 gal milk"]
    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": lines}
    )
    assert response.status_code == 201
    result = response.get_json()

    items_by_display = {i["display"]: i for i in result["items"]}

    # Check flour has quantity and unit
    flour = items_by_display["flour"]
    assert flour["quantity"] == 2.0
    assert flour["unit"] == "cup"

    # Check milk has quantity and unit
    milk = items_by_display["milk"]
    assert milk["quantity"] == 1.0
    assert milk["unit"] == "gallon"


def test_bulk_items_skips_blank_lines(auth_client, app, gid):
    """Bulk-items should skip blank lines."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Blank Test"})
    sl_id = sl_response.get_json()["id"]

    lines = ["2 cups flour", "", "1 gal milk", "   ", "salt"]
    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": lines}
    )
    assert response.status_code == 201
    result = response.get_json()

    assert result["added"] == 3, f"Expected 3 items, got {result['added']}"
    assert len(result["items"]) == 3


def test_bulk_items_position_ordering(auth_client, app, gid):
    """Bulk-items should assign correct position values."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Position Test"})
    sl_id = sl_response.get_json()["id"]

    # Add some initial items
    auth_client.post(f"/api/v1/shopping-lists/{sl_id}/items", json={"display": "Initial Item"})

    # Bulk add items
    lines = ["2 cups flour", "1 gal milk"]
    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": lines}
    )
    assert response.status_code == 201

    # Fetch the full list to verify positions
    full_list = auth_client.get(f"/api/v1/shopping-lists/{sl_id}").get_json()
    items = sorted(full_list["items"], key=lambda x: x["position"])

    assert items[0]["display"] == "Initial Item"
    assert items[0]["position"] == 0
    assert items[1]["display"] == "flour"
    assert items[1]["position"] == 1
    assert items[2]["display"] == "milk"
    assert items[2]["position"] == 2


def test_bulk_items_empty_lines_list(auth_client, app, gid):
    """Bulk-items with empty lines array should return 0 added."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Empty Lines"})
    sl_id = sl_response.get_json()["id"]

    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": []}
    )
    assert response.status_code == 201
    result = response.get_json()
    assert result["added"] == 0
    assert result["items"] == []


def test_bulk_items_single_item(auth_client, app, gid):
    """Bulk-items should work with a single line."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Single Item"})
    sl_id = sl_response.get_json()["id"]

    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": ["2 cups flour"]}
    )
    assert response.status_code == 201
    result = response.get_json()

    assert result["added"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["display"] == "flour"
    assert result["items"][0]["quantity"] == 2.0
    assert result["items"][0]["unit"] == "cup"


def test_bulk_items_with_fractions(auth_client, app, gid):
    """Bulk-items should handle fractional quantities."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Fractions"})
    sl_id = sl_response.get_json()["id"]

    lines = ["1 1/2 cups flour", "3/4 tsp salt"]
    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": lines}
    )
    assert response.status_code == 201
    result = response.get_json()

    items_by_display = {i["display"]: i for i in result["items"]}

    flour = items_by_display["flour"]
    assert flour["quantity"] == 1.5

    salt = items_by_display["salt"]
    assert salt["quantity"] == 0.75


def test_bulk_items_unicode_fractions(auth_client, app, gid):
    """Bulk-items should handle unicode fractions like ½."""
    sl_response = auth_client.post("/api/v1/shopping-lists", json={"name": "Unicode Fractions"})
    sl_id = sl_response.get_json()["id"]

    lines = ["½ cup butter", "1 ½ cups flour"]
    response = auth_client.post(
        f"/api/v1/shopping-lists/{sl_id}/bulk-items",
        json={"lines": lines}
    )
    assert response.status_code == 201
    result = response.get_json()

    items_by_display = {i["display"]: i for i in result["items"]}

    butter = items_by_display["butter"]
    assert butter["quantity"] == 0.5

    flour = items_by_display["flour"]
    assert flour["quantity"] == 1.5
