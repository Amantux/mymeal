"""Voice intents + response services for the myMeal cooking assistant.

Both the Assist intents ("what's for dinner?", "what can I cook?", "add eggs to
my shopping list") and the equivalent ``mymeal.*`` services call the myMeal API
through the coordinator and share the same phrasing.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv, intent

from .const import (
    DOMAIN,
    INTENT_ADD_TO_LIST,
    INTENT_WHATS_FOR_DINNER,
    INTENT_WHAT_CAN_I_COOK,
    SERVICE_ADD_TO_LIST,
    SERVICE_PLAN_WEEK,
    SERVICE_WHAT_CAN_I_COOK,
    SERVICE_WHATS_FOR_DINNER,
)

_REGISTERED = f"{DOMAIN}_services_registered"


def _first_coordinator(hass: HomeAssistant):
    for value in hass.data.get(DOMAIN, {}).values():
        if hasattr(value, "whats_for_dinner"):
            return value
    return None


def speak_dinner(status: dict) -> str:
    if status.get("status") != "ok":
        return "I couldn't reach myMeal to check the plan."
    meals = status.get("meals", [])
    if not meals:
        return "Nothing is planned to eat for that day yet."
    parts = [f"{m['mealType']}, {m['name']}" for m in meals if m.get("name")]
    return "On the menu: " + "; ".join(parts) + "."


def speak_suggestions(status: dict) -> str:
    if status.get("status") != "ok":
        return "I couldn't reach myMeal for suggestions."
    s = status.get("suggestions", [])
    if not s:
        return "I don't have any recipes to match against your pantry yet."
    top = s[0]
    line = f"You could make {top['name']} — you have {top['haveCount']} of " \
           f"{top['totalCount']} ingredients."
    if len(s) > 1:
        line += f" I found {len(s)} options in total."
    return line


def speak_added(status: dict) -> str:
    item = status.get("item", "that")
    if status.get("status") == "ok":
        return f"Added {item} to your shopping list."
    return f"Sorry, I couldn't add {item} to your list."


class WhatsForDinnerIntentHandler(intent.IntentHandler):
    intent_type = INTENT_WHATS_FOR_DINNER
    slot_schema = {vol.Optional("day"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        response = intent_obj.create_response()
        coordinator = _first_coordinator(hass)
        if coordinator is None:
            response.async_set_speech("myMeal isn't set up yet.")
            return response
        day = (intent_obj.slots.get("day") or {}).get("value", "")
        status = await coordinator.whats_for_dinner(day)
        response.async_set_speech(speak_dinner(status))
        return response


class WhatCanICookIntentHandler(intent.IntentHandler):
    intent_type = INTENT_WHAT_CAN_I_COOK

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        response = intent_obj.create_response()
        coordinator = _first_coordinator(hass)
        if coordinator is None:
            response.async_set_speech("myMeal isn't set up yet.")
            return response
        status = await coordinator.what_can_i_cook()
        response.async_set_speech(speak_suggestions(status))
        return response


class AddToListIntentHandler(intent.IntentHandler):
    intent_type = INTENT_ADD_TO_LIST
    slot_schema = {vol.Required("item"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        response = intent_obj.create_response()
        coordinator = _first_coordinator(hass)
        if coordinator is None:
            response.async_set_speech("myMeal isn't set up yet.")
            return response
        item = intent_obj.slots["item"]["value"]
        status = await coordinator.add_to_shopping_list(item)
        response.async_set_speech(speak_added(status))
        return response


async def async_register(hass: HomeAssistant) -> None:
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    intent.async_register(hass, WhatsForDinnerIntentHandler())
    intent.async_register(hass, WhatCanICookIntentHandler())
    intent.async_register(hass, AddToListIntentHandler())

    async def _dinner(call: ServiceCall) -> dict:
        coordinator = _first_coordinator(hass)
        status = (
            await coordinator.whats_for_dinner(call.data.get("day", ""))
            if coordinator
            else {}
        )
        return {**status, "speech": speak_dinner(status)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_WHATS_FOR_DINNER,
        _dinner,
        schema=vol.Schema({vol.Optional("day"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )

    async def _cook(call: ServiceCall) -> dict:
        coordinator = _first_coordinator(hass)
        status = await coordinator.what_can_i_cook() if coordinator else {}
        return {**status, "speech": speak_suggestions(status)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_WHAT_CAN_I_COOK,
        _cook,
        supports_response=SupportsResponse.ONLY,
    )

    async def _add(call: ServiceCall) -> dict:
        coordinator = _first_coordinator(hass)
        if coordinator is None:
            return {"status": "error", "speech": "myMeal isn't set up yet."}
        status = await coordinator.add_to_shopping_list(call.data["item"])
        return {**status, "speech": speak_added(status)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_TO_LIST,
        _add,
        schema=vol.Schema({vol.Required("item"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _plan(call: ServiceCall) -> dict:
        coordinator = _first_coordinator(hass)
        if coordinator is None:
            return {"status": "error"}
        return await coordinator.plan_week(
            int(call.data.get("days", 7)), call.data.get("preferences", "")
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAN_WEEK,
        _plan,
        schema=vol.Schema(
            {
                vol.Optional("days", default=7): vol.All(int, vol.Range(min=1, max=14)),
                vol.Optional("preferences", default=""): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


def async_unregister(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_WHATS_FOR_DINNER,
        SERVICE_WHAT_CAN_I_COOK,
        SERVICE_ADD_TO_LIST,
        SERVICE_PLAN_WEEK,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    hass.data.pop(_REGISTERED, None)
