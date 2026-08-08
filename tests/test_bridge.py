"""
Regression tests for BluettiBridge.poll_device()'s failure accounting.

bluetti_bt_lib's DeviceReader.read() swallows internal BLE errors and
returns None instead of raising (see _async_send_command's bare `except`).
poll_device() must treat a None result as a failure, or the consecutive
-failure counter never advances and the reader-recreation / hard-restart
recovery paths never trigger (the bug that let the bridge wedge for days).
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import bluetti_bridge  # noqa: E402


def make_bridge(hard_restart_after: int = 5) -> bluetti_bridge.BluettiBridge:
    config = {
        "mqtt": {"host": "localhost"},
        "bluetti": {
            # Must match DEVICE_NAME_RE in bluetti_bt_lib.devices (real
            # device name format, per config.yaml).
            "name": "AC702437004554229",
            "address": "AA:BB:CC:DD:EE:FF",
            "hard_restart_after_failures": hard_restart_after,
        },
    }
    return bluetti_bridge.BluettiBridge(config)


class FakeReader:
    """Stands in for DeviceReader; read() replays a scripted sequence."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def read(self):
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_none_result_counts_as_a_failure():
    bridge = make_bridge()
    bridge._reader = FakeReader([None])

    data = await bridge.poll_device()

    assert data is None
    assert bridge._consecutive_failures == 1


@pytest.mark.asyncio
async def test_successful_read_resets_failure_counter():
    bridge = make_bridge()
    bridge._consecutive_failures = 2
    bridge._reader = FakeReader([{"total_battery_percent": 42}])

    data = await bridge.poll_device()

    assert data == {"total_battery_percent": 42}
    assert bridge._consecutive_failures == 0


@pytest.mark.asyncio
async def test_reader_is_recreated_after_three_consecutive_failures():
    bridge = make_bridge(hard_restart_after=100)
    reader = FakeReader([None, None, None])
    bridge._reader = reader

    for _ in range(3):
        await bridge.poll_device()

    assert bridge._consecutive_failures == 3
    assert bridge._reader is None  # dropped so _make_reader() rebuilds it
    assert reader.calls == 3


@pytest.mark.asyncio
async def test_hard_restart_triggers_after_threshold_even_without_exceptions():
    """The scenario that actually wedged the service: read() only ever
    returns None (never raises), so the fix must still reach the
    hard-restart threshold purely from None results."""
    bridge = make_bridge(hard_restart_after=5)

    exit_calls = []
    original_exit = os._exit
    os._exit = lambda code: exit_calls.append(code) or (_ for _ in ()).throw(
        SystemExit(code)
    )
    try:
        for i in range(5):
            bridge._reader = bridge._reader or FakeReader([None])
            if i < 4:
                await bridge.poll_device()
            else:
                with pytest.raises(SystemExit):
                    await bridge.poll_device()
    finally:
        os._exit = original_exit

    assert bridge._consecutive_failures == 5
    assert exit_calls == [1]


@pytest.mark.asyncio
async def test_read_exception_still_counts_as_failure():
    bridge = make_bridge()
    bridge._reader = FakeReader([ConnectionError("BLE disconnected")])

    data = await bridge.poll_device()

    assert data is None
    assert bridge._consecutive_failures == 1
