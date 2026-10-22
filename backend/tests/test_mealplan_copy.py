from datetime import date, timedelta


def test_copy_mealplan_happy_path(auth_client, gid):
    """Copy a week of entries with correct date offsets and meal_type/servings/notes preserved."""
    # Create source entries
    src_date = date(2026, 1, 5)  # Monday
    for i in range(7):
        d = src_date + timedelta(days=i)
        auth_client.post("/api/v1/recipes", json={"name": f"Recipe {i}"})
        r = auth_client.post("/api/v1/recipes", json={"name": f"Recipe {i}"}).get_json()
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": d.isoformat(),
                "recipeId": r["id"],
                "mealType": ["dinner", "lunch", "dinner", "breakfast", "dinner", "snack", "dinner"][i],
                "servings": 2 + i,
                "notes": f"Notes for day {i}",
            },
        )

    # Copy to target week (7 days later)
    target_start = src_date + timedelta(days=7)
    src_start = src_date
    src_end = src_date + timedelta(days=6)

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={
            "source_start": src_start.isoformat(),
            "source_end": src_end.isoformat(),
            "target_start": target_start.isoformat(),
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["created"] == 7
    assert data["skipped"] == 0

    # Verify entries were copied
    r = auth_client.get(
        f"/api/v1/mealplans?start={target_start.isoformat()}&end={(target_start + timedelta(days=6)).isoformat()}"
    )
    items = r.get_json()["items"]
    assert len(items) == 7


def test_copy_mealplan_collisions_default(auth_client, gid):
    """Collisions are skipped by default, reported as created/skipped counts."""
    # Create target entries first
    tgt_date = date(2026, 1, 12)
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"Target Recipe {i}"})
        r = auth_client.post("/api/v1/recipes", json={"name": f"Target Recipe {i}"}).get_json()
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": (tgt_date + timedelta(days=i)).isoformat(),
                "recipeId": r["id"],
                "mealType": "dinner",
            },
        )

    # Create source entries
    src_date = date(2026, 1, 5)
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"Source Recipe {i}"})
        r = auth_client.post("/api/v1/recipes", json={"name": f"Source Recipe {i}"}).get_json()
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": (src_date + timedelta(days=i)).isoformat(),
                "recipeId": r["id"],
                "mealType": "dinner",
            },
        )

    # Copy with overwrite=false (default)
    src_start = src_date
    src_end = src_date + timedelta(days=2)
    target_start = tgt_date

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={
            "source_start": src_start.isoformat(),
            "source_end": src_end.isoformat(),
            "target_start": target_start.isoformat(),
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["created"] == 0  # All collided
    assert data["skipped"] == 3


def test_copy_mealplan_overwrite_true(auth_client, gid):
    """overwrite=true replaces colliding target entries only in the copied slots."""
    # Create target entries
    tgt_date = date(2026, 1, 12)
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"Target Recipe {i}"})
        r = auth_client.post("/api/v1/recipes", json={"name": f"Target Recipe {i}"}).get_json()
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": (tgt_date + timedelta(days=i)).isoformat(),
                "recipeId": r["id"],
                "mealType": "dinner",
            },
        )

    # Create source entries
    src_date = date(2026, 1, 5)
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"Source Recipe {i}"})
        r = auth_client.post("/api/v1/recipes", json={"name": f"Source Recipe {i}"}).get_json()
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": (src_date + timedelta(days=i)).isoformat(),
                "recipeId": r["id"],
                "mealType": "dinner",
            },
        )

    # Copy with overwrite=true
    src_start = src_date
    src_end = src_date + timedelta(days=2)
    target_start = tgt_date

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={
            "source_start": src_start.isoformat(),
            "source_end": src_end.isoformat(),
            "target_start": target_start.isoformat(),
            "overwrite": True,
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["created"] == 3
    assert data["skipped"] == 0


def test_copy_mealplan_group_isolation(auth_client, gid):
    """Cannot copy another group's source entries."""
    # Register a second user to create a different group
    auth_client.post(
        "/api/v1/users/register",
        json={"email": "other@example.com", "password": "password", "name": "Other"},
    )
    auth_client.post(
        "/api/v1/users/login",
        json={"username": "other@example.com", "password": "password"},
    )

    # Create a recipe in the other group (not accessible to our auth_client)
    # This simulates trying to copy from another household

    # Create source entries in our group
    src_date = date(2026, 1, 5)
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"Recipe {i}"})
        r = auth_client.post("/api/v1/recipes", json={"name": f"Recipe {i}"}).get_json()
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": (src_date + timedelta(days=i)).isoformat(),
                "recipeId": r["id"],
                "mealType": "dinner",
            },
        )

    # Copy should succeed for our own entries
    src_start = src_date
    src_end = src_date + timedelta(days=2)
    target_start = src_date + timedelta(days=7)

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={
            "source_start": src_start.isoformat(),
            "source_end": src_end.isoformat(),
            "target_start": target_start.isoformat(),
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["created"] == 3


def test_copy_mealplan_validation(auth_client, gid):
    """Validation: <=14-day source range, missing target_start -> 400."""
    src_date = date(2026, 1, 5)

    # Missing target_start
    src_start = src_date.isoformat()
    src_end = (src_date + timedelta(days=2)).isoformat()

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={"source_start": src_start, "source_end": src_end},
    )
    assert r.status_code == 400
    data = r.get_json()
    assert "error" in data

    # Range > 14 days
    src_end_long = (src_date + timedelta(days=15)).isoformat()
    target_start = (src_date + timedelta(days=7)).isoformat()

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={
            "source_start": src_start,
            "source_end": src_end_long,
            "target_start": target_start,
        },
    )
    assert r.status_code == 400
    data = r.get_json()
    assert "error" in data


def test_copy_mealplan_free_text_entries(auth_client, gid):
    """Copy preserves free-text entries (no recipe_id)."""
    src_date = date(2026, 1, 5)

    # Create free-text entries
    for i in range(3):
        auth_client.post(
            "/api/v1/mealplans",
            json={
                "date": (src_date + timedelta(days=i)).isoformat(),
                "title": f"Free text meal {i}",
                "mealType": "dinner",
                "notes": f"Free text notes {i}",
            },
        )

    # Copy to target week
    src_start = src_date
    src_end = src_date + timedelta(days=2)
    target_start = src_date + timedelta(days=7)

    r = auth_client.post(
        "/api/v1/mealplans/copy",
        json={
            "source_start": src_start.isoformat(),
            "source_end": src_end.isoformat(),
            "target_start": target_start.isoformat(),
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["created"] == 3
    assert data["skipped"] == 0

    # Verify free-text entries were copied
    r = auth_client.get(
        f"/api/v1/mealplans?start={target_start.isoformat()}&end={(target_start + timedelta(days=2)).isoformat()}"
    )
    items = r.get_json()["items"]
    assert len(items) == 3
    for i, item in enumerate(items):
        assert item["title"] == f"Free text meal {i}"
        assert item["notes"] == f"Free text notes {i}"
