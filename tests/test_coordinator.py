"""Tests for the IAlarmMkCoordinator."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from open_ialarm_mk_local_api import (
    AlarmStatusEnum,
    IAlarmMkAlarmError,
    IAlarmMkConnectionError,
    IAlarmMkLoginError,
    ZoneStatusEnum,
)
from open_ialarm_mk_local_api.models.alarm_status_model import AlarmStatusModel
from open_ialarm_mk_local_api.models.zone_model import ZoneModel

from custom_components.open_ialarm_mk.coordinator import IAlarmMkCoordinator, IAlarmMkData


@pytest.fixture
def coordinator(hass, mock_entry, mock_client, mock_network_info):
    """Return a coordinator with mocked client, not yet refreshed."""
    mock_entry.add_to_hass(hass)
    return IAlarmMkCoordinator(
        hass,
        mock_entry,
        mock_client,
        mock_network_info,
        scan_interval=30,
        model="MK7",
    )


# ── _async_update_data ──────────────────────────────────────────────────────

async def test_update_data_returns_status_and_zones(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.DISARMED)
    zone = ZoneModel(1, "Front Door", 1, ZoneStatusEnum.IN_USE)
    mock_client.get_zones.return_value = [zone]

    data = await coordinator._async_update_data()

    assert data.status.status == AlarmStatusEnum.DISARMED
    assert len(data.zones) == 1
    assert data.zones[0].index == 1


async def test_update_data_retries_on_unavailable(coordinator, mock_client):
    """UNAVAILABLE status → single retry → resolves on second call."""
    mock_client.get_status.side_effect = [
        AlarmStatusModel(status=AlarmStatusEnum.UNAVAILABLE),
        AlarmStatusModel(status=AlarmStatusEnum.DISARMED),
    ]
    mock_client.get_zones.return_value = []

    data = await coordinator._async_update_data()
    assert data.status.status == AlarmStatusEnum.DISARMED
    assert mock_client.get_status.call_count == 2


async def test_update_data_raises_update_failed_if_unavailable_after_retry(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.UNAVAILABLE)
    mock_client.get_zones.return_value = []

    with pytest.raises(UpdateFailed, match="UNAVAILABLE"):
        await coordinator._async_update_data()


async def test_update_data_raises_on_connection_error(coordinator, mock_client):
    mock_client.get_status.side_effect = IAlarmMkConnectionError("conn fail")
    mock_client.get_zones.side_effect = IAlarmMkConnectionError("conn fail")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_data_raises_on_login_error(coordinator, mock_client):
    mock_client.get_status.side_effect = IAlarmMkLoginError("auth fail")
    mock_client.get_zones.side_effect = IAlarmMkLoginError("auth fail")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_data_raises_on_unexpected_error(coordinator, mock_client):
    mock_client.get_status.side_effect = RuntimeError("boom")
    mock_client.get_zones.side_effect = RuntimeError("boom")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


# ── push_connected ──────────────────────────────────────────────────────────

def test_push_connected_false_when_no_push_client(coordinator):
    assert coordinator.push_connected is False


def test_push_connected_delegates_to_push_client(coordinator, mock_push_client):
    coordinator._push_client = mock_push_client
    mock_push_client.connected = True
    assert coordinator.push_connected is True

    mock_push_client.connected = False
    assert coordinator.push_connected is False


# ── start_push_client ───────────────────────────────────────────────────────

def test_start_push_client_sets_client(coordinator, mock_push_client):
    coordinator.start_push_client(mock_push_client)
    assert coordinator._push_client is mock_push_client
    assert coordinator._push_task is not None


# ── _async_handle_panel_event ───────────────────────────────────────────────

async def test_handle_panel_event_known_cid_updates_state(coordinator, mock_client):
    """Known CID → state updated via async_set_updated_data (no poll)."""
    zone = ZoneModel(1, "Zone", 1, ZoneStatusEnum.IN_USE)
    coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.DISARMED),
            zones=[zone],
        )
    )
    coordinator.async_set_updated_data = MagicMock()

    await coordinator._async_handle_panel_event({"Cid": 3401})  # ARMED_AWAY

    coordinator.async_set_updated_data.assert_called_once()
    args = coordinator.async_set_updated_data.call_args[0][0]
    assert args.status.status == AlarmStatusEnum.ARMED_AWAY


async def test_handle_panel_event_unknown_cid_triggers_refresh(coordinator, mock_client):
    """Unknown CID → falls back to poll."""
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.DISARMED)
    mock_client.get_zones.return_value = []

    await coordinator._async_handle_panel_event({"Cid": 9999})
    mock_client.get_status.assert_called()


async def test_handle_panel_event_triggered_saves_zone(coordinator, hass):
    """TRIGGERED event with Zone → zone saved and binary sensor marked open."""
    zone = ZoneModel(1, "Interna", 1, ZoneStatusEnum.IN_USE)
    coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.DISARMED),
            zones=[zone],
        )
    )
    events = []
    hass.bus.async_listen("open_ialarm_mk_alarm", lambda event: events.append(event.data))

    await coordinator._async_handle_panel_event(
        {"Cid": 1132, "Zone": 1, "ZoneName": "Interna", "Time": "2026-09-21 22:11:57"}
    )

    data = coordinator.data
    assert data.last_alarm_zone == 1
    assert data.last_alarm_zone_name == "Interna"
    assert data.last_alarm_cid == "1132"
    assert data.zones[0].status & ZoneStatusEnum.ALARM
    assert data.zones[0].status & ZoneStatusEnum.FAULT
    assert events and events[0]["zone"] == 1
    assert events[0]["zone_name"] == "Interna"


async def test_handle_panel_event_disarm_preserves_last_alarm(coordinator):
    """Disarm event must not clobber the last triggered zone."""
    coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.TRIGGERED),
            zones=[],
            last_alarm_zone=2,
            last_alarm_zone_name="SOGG. LATO",
            last_alarm_cid="1132",
        )
    )

    await coordinator._async_handle_panel_event({"Cid": 1401})  # DISARMED

    data = coordinator.data
    assert data.status.status == AlarmStatusEnum.DISARMED
    assert data.last_alarm_zone == 2
    assert data.last_alarm_zone_name == "SOGG. LATO"
    assert data.last_alarm_cid == "1132"


async def test_update_data_preserves_last_alarm_fields(coordinator, mock_client):
    """Polling must keep the zone from the last alarm."""
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.DISARMED)
    mock_client.get_zones.return_value = []
    coordinator.async_set_updated_data(
        IAlarmMkData(
            status=AlarmStatusModel(status=AlarmStatusEnum.DISARMED),
            zones=[],
            last_alarm_zone=2,
            last_alarm_zone_name="SOGG. LATO",
        )
    )

    data = await coordinator._async_update_data()

    assert data.status.status == AlarmStatusEnum.DISARMED
    assert data.last_alarm_zone == 2
    assert data.last_alarm_zone_name == "SOGG. LATO"


# ── alarm commands ──────────────────────────────────────────────────────────

async def test_async_arm_away_calls_client(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.ARMED_AWAY)
    mock_client.get_zones.return_value = []
    await coordinator.async_arm_away()
    mock_client.arm_away.assert_called_once()


async def test_async_arm_stay_calls_client(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.ARMED_STAY)
    mock_client.get_zones.return_value = []
    await coordinator.async_arm_stay()
    mock_client.arm_stay.assert_called_once()


async def test_async_arm_partial_calls_client(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.ARMED_PARTIAL)
    mock_client.get_zones.return_value = []
    await coordinator.async_arm_partial()
    mock_client.arm_partial.assert_called_once()


async def test_async_disarm_calls_client(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.DISARMED)
    mock_client.get_zones.return_value = []
    await coordinator.async_disarm()
    mock_client.disarm.assert_called_once()


async def test_async_cancel_alarm_calls_client(coordinator, mock_client):
    mock_client.get_status.return_value = AlarmStatusModel(status=AlarmStatusEnum.DISARMED)
    mock_client.get_zones.return_value = []
    await coordinator.async_cancel_alarm()
    mock_client.cancel_alarm.assert_called_once()


@pytest.mark.parametrize(
    "method,client_method",
    [
        ("async_arm_away", "arm_away"),
        ("async_arm_stay", "arm_stay"),
        ("async_arm_partial", "arm_partial"),
        ("async_disarm", "disarm"),
        ("async_cancel_alarm", "cancel_alarm"),
    ],
)
async def test_commands_raise_ha_error_on_alarm_error(coordinator, mock_client, method, client_method):
    getattr(mock_client, client_method).side_effect = IAlarmMkAlarmError("rejected")
    with pytest.raises(HomeAssistantError):
        await getattr(coordinator, method)()


# ── async_shutdown ──────────────────────────────────────────────────────────

async def test_async_shutdown_cancels_push_and_disconnects(coordinator, mock_client, mock_push_client):
    coordinator._push_client = mock_push_client
    task = asyncio.ensure_future(asyncio.sleep(100))
    coordinator._push_task = task

    await coordinator.async_shutdown()

    mock_push_client.cancel.assert_called_once()
    # cancel() schedules cancellation; give the loop one cycle to process it
    assert task.cancelling() > 0 or task.cancelled()
    mock_client.disconnect.assert_called_once()


async def test_async_shutdown_tolerates_disconnect_error(coordinator, mock_client):
    mock_client.disconnect.side_effect = Exception("oops")
    await coordinator.async_shutdown()  # must not raise
