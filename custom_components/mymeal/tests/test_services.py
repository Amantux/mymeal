"""Home Assistant service behaviour for recipe CRUD + versions/experiments.

These drive REAL `hass.services.async_call`s through the REAL coordinator with
only the HTTP layer mocked, so a regression in either the service schemas or the
coordinator's body-building fails a test. The two that matter most:

  * `update_recipe` must send ONLY the fields the caller supplied — the API
    replaces ingredients/steps whenever those keys are present, so a partial
    edit that leaked a default would silently wipe a stored list.
  * `delete_recipe` must refuse without an explicit confirmation, and must
    refuse an ambiguous name even WITH one. Deletes are irreversible; edits are
    recoverable from the auto-snapshot history.
"""
import os

import yaml
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mymeal import services
from custom_components.mymeal.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TOKEN,
    DOMAIN,
)
from custom_components.mymeal.coordinator import MyMealDataUpdateCoordinator

BASE = "http://127.0.0.1:7850"
RECIPES = f"{BASE}/api/v1/recipes"
SERVICES_YAML = os.path.join(os.path.dirname(__file__), "..", "services.yaml")

RECIPE = {"id": "r1", "name": "Soup", "servings": 4,
          "ingredients": [{"display": "2 onions"}], "steps": [{"text": "Chop"}]}


async def _setup(hass):
    """Register the services against a real coordinator (HTTP is mocked)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "http://127.0.0.1", CONF_PORT: 7850, CONF_TOKEN: ""},
    )
    entry.add_to_hass(hass)
    session = aiohttp_client.async_get_clientsession(hass)
    coordinator = MyMealDataUpdateCoordinator(hass, session, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await services.async_register(hass)
    return coordinator


def _bodies(aioclient_mock, method):
    """Request bodies sent with `method` ("put"/"post"/"delete")."""
    return [c[2] for c in aioclient_mock.mock_calls if c[0].lower() == method]


# --- registration ---------------------------------------------------------

async def test_every_documented_service_is_registered(hass):
    """services.yaml and the registrations must match 1:1 in both directions.

    An entry with no registration is a service the UI offers but that does
    nothing; a registration with no entry is invisible in Developer Tools.
    """
    await _setup(hass)
    with open(SERVICES_YAML) as fh:
        documented = set(yaml.safe_load(fh))
    registered = set(hass.services.async_services().get(DOMAIN, {}))

    assert documented - registered == set(), "documented but not registered"
    assert registered - documented == set(), "registered but not documented"
    # Guard against the set silently shrinking to nothing.
    assert "delete_recipe" in registered and "update_recipe" in registered


# --- update_recipe must not clobber --------------------------------------

async def test_update_recipe_omits_fields_the_caller_did_not_supply(
    hass, aioclient_mock
):
    """A name-only edit must not carry ingredients/steps into the PUT body."""
    aioclient_mock.get(f"{RECIPES}/r1", json=RECIPE)
    aioclient_mock.put(f"{RECIPES}/r1", json={"id": "r1", "name": "Better Soup"})
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN, "update_recipe",
        {"name_or_id": "r1", "name": "Better Soup"},
        blocking=True, return_response=True,
    )

    body = _bodies(aioclient_mock, "put")[0]
    assert body == {"name": "Better Soup"}
    assert "ingredients" not in body   # the whole point: no silent wipe
    assert "steps" not in body


async def test_update_recipe_with_an_explicit_empty_list_still_clears(
    hass, aioclient_mock
):
    """Passing [] deliberately IS a clear — only omission must be a no-op."""
    aioclient_mock.get(f"{RECIPES}/r1", json=RECIPE)
    aioclient_mock.put(f"{RECIPES}/r1", json={"id": "r1", "name": "Soup"})
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN, "update_recipe",
        {"name_or_id": "r1", "ingredients": []},
        blocking=True, return_response=True,
    )

    assert _bodies(aioclient_mock, "put")[0] == {"ingredients": []}


# --- delete_recipe requires confirmation ---------------------------------

async def test_delete_recipe_without_confirm_deletes_nothing(hass, aioclient_mock):
    aioclient_mock.get(f"{RECIPES}/r1", json=RECIPE)
    aioclient_mock.get(f"{RECIPES}/r1/versions", json={"items": [{"id": "v1"}]})
    await _setup(hass)

    result = await hass.services.async_call(
        DOMAIN, "delete_recipe",
        {"name_or_id": "r1", "confirm": False},
        blocking=True, return_response=True,
    )

    assert result["status"] == "confirm_required"
    assert result["name"] == "Soup"          # names what would be lost
    assert result["versions"] == 1           # and how many versions go with it
    assert not _bodies(aioclient_mock, "delete")


async def test_delete_recipe_with_confirm_deletes(hass, aioclient_mock):
    aioclient_mock.get(f"{RECIPES}/r1", json=RECIPE)
    aioclient_mock.delete(f"{RECIPES}/r1", json={})
    await _setup(hass)

    result = await hass.services.async_call(
        DOMAIN, "delete_recipe",
        {"name_or_id": "r1", "confirm": True},
        blocking=True, return_response=True,
    )

    assert result["status"] == "ok"
    assert result["deleted"] == "Soup"
    assert len(_bodies(aioclient_mock, "delete")) == 1


async def test_delete_recipe_refuses_an_ambiguous_name_even_when_confirmed(
    hass, aioclient_mock
):
    """Confirmation is not a licence to guess which recipe was meant."""
    aioclient_mock.get(f"{RECIPES}/chicken", status=404)
    aioclient_mock.get(
        RECIPES,
        json={"items": [{"id": "r1", "name": "Chicken Soup"},
                        {"id": "r2", "name": "Chicken Pie"}]},
    )
    await _setup(hass)

    result = await hass.services.async_call(
        DOMAIN, "delete_recipe",
        {"name_or_id": "chicken", "confirm": True},
        blocking=True, return_response=True,
    )

    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 2
    assert not _bodies(aioclient_mock, "delete")


# --- response shape -------------------------------------------------------

async def test_list_recipe_versions_returns_the_documented_shape(
    hass, aioclient_mock
):
    aioclient_mock.get(f"{RECIPES}/r1", json=RECIPE)
    aioclient_mock.get(
        f"{RECIPES}/r1/versions",
        json={"items": [
            {"id": "v1", "kind": "auto", "status": "open", "label": "Edit",
             "rating": None},
            {"id": "v2", "kind": "experiment", "status": "open",
             "label": "More thyme", "rating": 4},
        ]},
    )
    await _setup(hass)

    result = await hass.services.async_call(
        DOMAIN, "list_recipe_versions", {"name_or_id": "r1"},
        blocking=True, return_response=True,
    )

    assert result["status"] == "ok"
    assert result["recipe"] == "Soup"
    assert [v["id"] for v in result["versions"]] == ["v1", "v2"]
    assert result["versions"][1]["kind"] == "experiment"
    assert result["versions"][1]["rating"] == 4
