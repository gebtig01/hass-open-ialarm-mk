"""Alarm control panel entity for iAlarm-MK."""
from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from open_ialarm_mk_local_api import AlarmStatusEnum

from .const import DOMAIN
from .coordinator import IAlarmMkCoordinator

_STATUS_MAP: dict[AlarmStatusEnum, AlarmControlPanelState | None] = {
    AlarmStatusEnum.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    AlarmStatusEnum.DISARMED: AlarmControlPanelState.DISARMED,
    AlarmStatusEnum.ARMED_STAY: AlarmControlPanelState.ARMED_HOME,
    AlarmStatusEnum.TRIGGERED: AlarmControlPanelState.TRIGGERED,
    AlarmStatusEnum.ALARM_ARMING: AlarmControlPanelState.ARMING,
    AlarmStatusEnum.ARMED_PARTIAL: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    AlarmStatusEnum.CANCEL: AlarmControlPanelState.DISARMED,
    AlarmStatusEnum.UNAVAILABLE: None,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IAlarmMkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([IAlarmMkPanel(coordinator)])


class IAlarmMkPanel(CoordinatorEntity[IAlarmMkCoordinator], AlarmControlPanelEntity):
    """iAlarm-MK alarm control panel entity."""

    _attr_has_entity_name = True
    _attr_name = None  # entity name = device name
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
    )

    def __init__(self, coordinator: IAlarmMkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.network_info.mac}_alarm_panel"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.network_info.mac)},
            name=self.coordinator.network_info.name or "iAlarm-MK",
            manufacturer="Antifurto365 / Meian Technology",
            model=f"iAlarm {self.coordinator.model}",
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        if self.coordinator.data is None:
            return None
        return _STATUS_MAP.get(self.coordinator.data.status.status)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        return {
            "last_alarm_zone": data.last_alarm_zone,
            "last_alarm_zone_name": data.last_alarm_zone_name,
            "last_alarm_cid": data.last_alarm_cid,
            "last_alarm_time": data.last_alarm_time,
        }

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.async_disarm()

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self.coordinator.async_arm_away()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self.coordinator.async_arm_stay()

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self.coordinator.async_arm_partial()
