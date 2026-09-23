"""Tests for the alarm control panel entity."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)

from open_ialarm_mk_local_api import AlarmStatusEnum, ZoneStatusEnum
from open_ialarm_mk_local_api.models.alarm_status_model import AlarmStatusModel
from open_ialarm_mk_local_api.models.zone_model import ZoneModel

from custom_components.open_ialarm_mk.alarm_control_panel import IAlarmMkPanel, _STATUS_MAP
from custom_components.open_ialarm_mk.coordinator import IAlarmMkCoordinator, IAlarmMkData


@pytest.fixture
def panel(hass, mock_entry, mock_client, mock_network_info):
    mock_entry.add_to_hass(hass)
    coordinator = IAlarmMkCoordinator(hass, mock_entry, mock_client, mock_network_info, 30, "MK7")
    coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.DISARMED),
            zones=[],
        )
    )
    return IAlarmMkPanel(coordinator)


# ── status mapping ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "alarm_enum,expected_state",
    [
        (AlarmStatusEnum.ARMED_AWAY, AlarmControlPanelState.ARMED_AWAY),
        (AlarmStatusEnum.DISARMED, AlarmControlPanelState.DISARMED),
        (AlarmStatusEnum.ARMED_STAY, AlarmControlPanelState.ARMED_HOME),
        (AlarmStatusEnum.TRIGGERED, AlarmControlPanelState.TRIGGERED),
        (AlarmStatusEnum.ALARM_ARMING, AlarmControlPanelState.ARMING),
        (AlarmStatusEnum.ARMED_PARTIAL, AlarmControlPanelState.ARMED_CUSTOM_BYPASS),
        (AlarmStatusEnum.CANCEL, AlarmControlPanelState.DISARMED),
        (AlarmStatusEnum.UNAVAILABLE, None),
    ],
)
def test_status_map_covers_all_enums(alarm_enum, expected_state):
    assert _STATUS_MAP[alarm_enum] == expected_state


def test_alarm_state_reflects_coordinator(panel):
    assert panel.alarm_state == AlarmControlPanelState.DISARMED


def test_alarm_state_none_when_no_data(panel):
    panel.coordinator.async_set_updated_data(None)
    assert panel.alarm_state is None


def test_alarm_state_armed_away(panel):
    panel.coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.ARMED_AWAY),
            zones=[],
        )
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY


def test_alarm_state_triggered(panel):
    panel.coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.TRIGGERED),
            zones=[],
        )
    )
    assert panel.alarm_state == AlarmControlPanelState.TRIGGERED


# ── entity attributes ───────────────────────────────────────────────────────

def test_unique_id_contains_mac(panel, mock_network_info):
    assert mock_network_info.mac in panel.unique_id


def test_no_code_required(panel):
    assert panel.code_arm_required is False


def test_supported_features(panel):
    assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_HOME
    assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_AWAY
    assert panel.supported_features & AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS


def test_device_info_identifiers(panel, mock_network_info):
    from custom_components.open_ialarm_mk.const import DOMAIN
    assert (DOMAIN, mock_network_info.mac) in panel.device_info["identifiers"]


def test_extra_state_attributes_none_when_no_data(panel):
    panel.coordinator.async_set_updated_data(None)
    assert panel.extra_state_attributes is None


def test_extra_state_attributes_reflect_last_alarm(panel):
    panel.coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.TRIGGERED),
            zones=[],
            last_alarm_zone=2,
            last_alarm_zone_name="SOGG. LATO",
            last_alarm_cid="1132",
            last_alarm_time="2026-09-21 22:11:57",
        )
    )
    attrs = panel.extra_state_attributes
    assert attrs is not None
    assert attrs["last_alarm_zone"] == 2
    assert attrs["last_alarm_zone_name"] == "SOGG. LATO"
    assert attrs["last_alarm_cid"] == "1132"


# ── command delegation ──────────────────────────────────────────────────────

async def test_async_alarm_disarm_delegates(panel, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.DISARMED)
    mock_client.get_zones.return_value = []
    await panel.async_alarm_disarm()
    mock_client.disarm.assert_called_once()


async def test_async_alarm_arm_away_delegates(panel, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.ARMED_AWAY)
    mock_client.get_zones.return_value = []
    await panel.async_alarm_arm_away()
    mock_client.arm_away.assert_called_once()


async def test_async_alarm_arm_home_delegates(panel, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.ARMED_STAY)
    mock_client.get_zones.return_value = []
    await panel.async_alarm_arm_home()
    mock_client.arm_stay.assert_called_once()


async def test_async_alarm_arm_custom_bypass_delegates(panel, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.ARMED_PARTIAL)
    mock_client.get_zones.return_value = []
    await panel.async_alarm_arm_custom_bypass()
    mock_client.arm_partial.assert_called_once()
