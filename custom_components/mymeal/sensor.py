from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MyMealDataUpdateCoordinator
from .entity import device_info


@dataclass(frozen=True, kw_only=True)
class MyMealSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], object] = lambda data: None
    attrs_fn: Callable[[dict], dict] | None = None


def _totals(d: dict) -> dict:
    return d.get("totals", {})


SENSORS: tuple[MyMealSensorDescription, ...] = (
    MyMealSensorDescription(
        key="recipes", name="Recipes", icon="mdi:book-open-variant",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("recipes"),
    ),
    MyMealSensorDescription(
        key="meals_this_week", name="Meals planned this week",
        icon="mdi:calendar-check", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("mealsThisWeek"),
        attrs_fn=lambda d: {"week": d.get("weekPlan", [])},
    ),
    MyMealSensorDescription(
        key="todays_meals", name="Today's meals", icon="mdi:silverware-fork-knife",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.get("todaysMeals", [])),
        attrs_fn=lambda d: {"meals": d.get("todaysMeals", [])},
    ),
    MyMealSensorDescription(
        key="shopping_items", name="Shopping list items", icon="mdi:cart",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("shoppingItems"),
    ),
    # Inventory (pantry) sensors were removed: myMeal no longer owns inventory —
    # it is provided by the companion Edibl app, which ships its own HA
    # integration with freshness/expiry sensors.
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MyMealDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(MyMealSensor(coordinator, entry, d) for d in SENSORS)


class MyMealSensor(CoordinatorEntity[MyMealDataUpdateCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: MyMealSensorDescription

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self):
        if self.entity_description.attrs_fn:
            return self.entity_description.attrs_fn(self.coordinator.data or {})
        return None
