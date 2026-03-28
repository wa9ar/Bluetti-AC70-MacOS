"""
Bluetti AC70 -> MQTT Bridge
Runs natively on macOS (direct BLE access), publishes to Home Assistant via MQTT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import aiomqtt
import yaml
from bleak import BleakScanner
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bluetti_bt_lib import build_device, DeviceReader, DeviceReaderConfig, DeviceWriter, recognize_device

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# ─────────────────────────────────────────────
# AC70 entity definitions
# ─────────────────────────────────────────────

SENSORS = [
    {
        "key": "total_battery_percent",
        "name": "Battery SOC",
        "unit": "%",
        "device_class": "battery",
        "state_class": "measurement",
    },
    {
        "key": "time_remaining",
        "name": "Time Remaining",
        "unit": "h",
        "device_class": "duration",
        "state_class": "measurement",
    },
    {
        "key": "ac_output_power",
        "name": "AC Output Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
    },
    {
        "key": "dc_output_power",
        "name": "DC Output Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
    },
    {
        "key": "ac_input_power",
        "name": "AC Input Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
    },
    {
        "key": "dc_input_power",
        "name": "DC Input Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
    },
    {
        "key": "ac_output_voltage",
        "name": "AC Output Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
    },
    {
        "key": "ac_output_frequency",
        "name": "AC Output Frequency",
        "unit": "Hz",
        "device_class": "frequency",
        "state_class": "measurement",
    },
    {
        "key": "ac_input_voltage",
        "name": "AC Input Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
    },
    {
        "key": "ac_input_current",
        "name": "AC Input Current",
        "unit": "A",
        "device_class": "current",
        "state_class": "measurement",
    },
    {
        "key": "ac_input_frequency",
        "name": "AC Input Frequency",
        "unit": "Hz",
        "device_class": "frequency",
        "state_class": "measurement",
    },
    {
        "key": "dc_input_voltage",
        "name": "DC Input Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
    },
    {
        "key": "dc_input_current",
        "name": "DC Input Current",
        "unit": "A",
        "device_class": "current",
        "state_class": "measurement",
    },
    {
        "key": "ctrl_eco_min_power_dc",
        "name": "ECO DC Min Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "category": "diagnostic",
    },
    {
        "key": "ctrl_eco_min_power_ac",
        "name": "ECO AC Min Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "category": "diagnostic",
    },
    {
        "key": "version_bms",
        "name": "BMS Version",
        "unit": None,
        "device_class": None,
        "state_class": None,
        "category": "diagnostic",
    },
]

SWITCHES = [
    {"key": "ctrl_ac", "name": "AC Output"},
    {"key": "ctrl_dc", "name": "DC Output"},
    {"key": "ctrl_eco_ac", "name": "ECO Mode AC"},
    {"key": "ctrl_eco_dc", "name": "ECO Mode DC"},
    {"key": "ctrl_power_lifting", "name": "Power Lifting"},
]

SELECTS = [
    {
        "key": "ctrl_charging_mode",
        "name": "Charging Mode",
        "options": ["STANDARD", "SILENT", "TURBO"],
    },
    {
        "key": "ctrl_eco_time_mode_ac",
        "name": "ECO AC Time Mode",
        "options": ["HOURS1", "HOURS2", "HOURS3", "HOURS4"],
    },
    {
        "key": "ctrl_eco_time_mode_dc",
        "name": "ECO DC Time Mode",
        "options": ["HOURS1", "HOURS2", "HOURS3", "HOURS4"],
    },
]

SWITCH_KEYS = {sw["key"] for sw in SWITCHES}
SELECT_KEYS = {sel["key"] for sel in SELECTS}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def serialize_value(val: Any) -> Any:
    """Convert values to JSON-serializable types."""
    if isinstance(val, bool):
        return val
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, Enum):
        return val.name
    return val


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


# ─────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────


class BluettiBridge:
    def __init__(self, config: dict) -> None:
        self.cfg = config
        self.mqtt_cfg = config["mqtt"]
        self.bt_cfg = config["bluetti"]

        self.device_id = self.bt_cfg["name"].lower().replace(" ", "_")
        self.state_topic = f"bluetti/{self.device_id}/state"
        self.avail_topic = f"bluetti/{self.device_id}/availability"
        self.cmd_base = f"bluetti/{self.device_id}/command"

        self.logger = logging.getLogger("BluettiBridge")
        self.lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._reader: DeviceReader | None = None
        self._use_encryption: bool | None = None  # detected at startup

        self.bluetti_device = build_device(self.bt_cfg["name"])
        if self.bluetti_device is None:
            raise ValueError(
                f"Unknown device: {self.bt_cfg['name']}. "
                "Check that the name matches exactly (e.g. AC70)."
            )

    # ── MQTT Discovery ─────────────────────────────

    def _device_payload(self) -> dict:
        return {
            "identifiers": [f"bluetti_{self.device_id}"],
            "name": f"Bluetti {self.bt_cfg['name']}",
            "manufacturer": "Bluetti",
            "model": self.bt_cfg["name"],
        }

    async def publish_discovery(self, client: aiomqtt.Client) -> None:
        """Publish MQTT auto-discovery messages for Home Assistant."""
        device = self._device_payload()

        for s in SENSORS:
            uid = f"bluetti_{self.device_id}_{s['key']}"
            payload: dict[str, Any] = {
                "name": s["name"],
                "unique_id": uid,
                "state_topic": self.state_topic,
                "value_template": f"{{{{ value_json.{s['key']} }}}}",
                "availability_topic": self.avail_topic,
                "device": device,
            }
            if s.get("unit"):
                payload["unit_of_measurement"] = s["unit"]
            if s.get("device_class"):
                payload["device_class"] = s["device_class"]
            if s.get("state_class"):
                payload["state_class"] = s["state_class"]
            if s.get("category"):
                payload["entity_category"] = s["category"]
            await client.publish(
                f"homeassistant/sensor/{uid}/config",
                json.dumps(payload),
                retain=True,
            )

        for sw in SWITCHES:
            uid = f"bluetti_{self.device_id}_{sw['key']}"
            payload = {
                "name": sw["name"],
                "unique_id": uid,
                "state_topic": self.state_topic,
                "value_template": (
                    f"{{% if value_json.{sw['key']} %}}ON{{% else %}}OFF{{% endif %}}"
                ),
                "command_topic": f"{self.cmd_base}/{sw['key']}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "availability_topic": self.avail_topic,
                "device": device,
            }
            await client.publish(
                f"homeassistant/switch/{uid}/config",
                json.dumps(payload),
                retain=True,
            )

        for sel in SELECTS:
            uid = f"bluetti_{self.device_id}_{sel['key']}"
            payload = {
                "name": sel["name"],
                "unique_id": uid,
                "state_topic": self.state_topic,
                "value_template": f"{{{{ value_json.{sel['key']} }}}}",
                "command_topic": f"{self.cmd_base}/{sel['key']}",
                "options": sel["options"],
                "availability_topic": self.avail_topic,
                "device": device,
            }
            await client.publish(
                f"homeassistant/select/{uid}/config",
                json.dumps(payload),
                retain=True,
            )

        self.logger.info(
            "Discovery published: %d sensors, %d switches, %d selects",
            len(SENSORS),
            len(SWITCHES),
            len(SELECTS),
        )

    # ── BLE encryption detection ───────────────────

    async def detect_encryption(self) -> None:
        """Auto-detect whether the AC70 uses BLE encryption."""
        self.logger.info("Detecting BLE encryption...")
        loop = asyncio.get_running_loop()
        try:
            result = await recognize_device(self.bt_cfg["address"], loop.create_future)
            if result is not None:
                self._use_encryption = result.encrypted
                self.logger.info(
                    "Device: %s | IoT v%d | Encryption: %s",
                    result.name,
                    result.iot_version,
                    result.encrypted,
                )
            else:
                self._use_encryption = False
                self.logger.warning("recognize_device failed — encryption disabled by default")
        except Exception as exc:
            self._use_encryption = False
            self.logger.warning("Encryption detection error: %s — disabled by default", exc)

    # ── BLE read ───────────────────────────────────

    def _make_reader(self) -> DeviceReader:
        loop = asyncio.get_running_loop()
        return DeviceReader(
            self.bt_cfg["address"],
            self.bluetti_device,
            loop.create_future,
            DeviceReaderConfig(
                self.bt_cfg.get("polling_timeout", 15),
                self._use_encryption or False,
            ),
            self.lock,
        )

    async def poll_device(self) -> dict | None:
        """Read all data from the AC70 via BLE."""
        if self._reader is None:
            self._reader = self._make_reader()
        try:
            data = await self._reader.read()
            self._consecutive_failures = 0
            return data
        except Exception as exc:
            self._consecutive_failures += 1
            self.logger.warning(
                "BLE read failed (%d consecutive): %s",
                self._consecutive_failures,
                exc,
            )
            # Recreate reader after 3 consecutive failures
            if self._consecutive_failures >= 3:
                self.logger.info("Recreating DeviceReader...")
                self._reader = None
            return None

    # ── BLE write ──────────────────────────────────

    async def write_to_device(self, field_name: str, value: Any) -> None:
        """Send a command to the AC70 via BLE."""
        try:
            ble_device = await BleakScanner.find_device_by_address(
                self.bt_cfg["address"], timeout=5
            )
            if ble_device is None:
                self.logger.error("Device not found for write")
                return

            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or "AC70",
                max_attempts=5,
            )
            if not client.is_connected:
                self.logger.error("BLE connection failed for write")
                return

            writer = DeviceWriter(client, self.bluetti_device, lock=self.lock)
            async with asyncio.timeout(15):
                await writer.write(field_name, value)
                await asyncio.sleep(3)  # Wait for device to apply the change

            await client.disconnect()
            self.logger.info("Command sent: %s = %s", field_name, value)

        except TimeoutError:
            self.logger.error("BLE write timeout for %s", field_name)
        except Exception as exc:
            self.logger.error("Write error for %s: %s", field_name, exc)

    # ── MQTT command handler ───────────────────────

    async def handle_command(self, topic: str, payload: str) -> None:
        """Handle an incoming MQTT command."""
        field_name = topic.split("/")[-1]
        self.logger.info("Command received: %s = %s", field_name, payload)

        if field_name in SWITCH_KEYS:
            await self.write_to_device(field_name, payload.upper() == "ON")
        elif field_name in SELECT_KEYS:
            await self.write_to_device(field_name, payload)
        else:
            self.logger.warning("Unknown command field: %s", field_name)

    # ── Main loop ──────────────────────────────────

    async def run(self) -> None:
        """Start the MQTT bridge. Reconnects automatically on error."""
        mqtt_kwargs: dict[str, Any] = {
            "hostname": self.mqtt_cfg["host"],
            "port": self.mqtt_cfg.get("port", 1883),
        }
        if self.mqtt_cfg.get("username"):
            mqtt_kwargs["username"] = self.mqtt_cfg["username"]
            mqtt_kwargs["password"] = self.mqtt_cfg.get("password", "")

        interval = self.bt_cfg.get("polling_interval", 30)
        command_topics = [f"{self.cmd_base}/{k}" for k in SWITCH_KEYS | SELECT_KEYS]

        self.logger.info(
            "Bridge started — device: %s @ %s",
            self.bt_cfg["name"],
            self.bt_cfg["address"],
        )

        await self.detect_encryption()

        while True:
            try:
                async with aiomqtt.Client(**mqtt_kwargs) as client:
                    await self.publish_discovery(client)
                    await client.publish(self.avail_topic, "online", retain=True)
                    self.logger.info("MQTT connected, device online")

                    for topic in command_topics:
                        await client.subscribe(topic)

                    async def poll_loop() -> None:
                        while True:
                            self.logger.debug("Polling AC70...")
                            data = await self.poll_device()

                            if data is not None:
                                state = {
                                    k: serialize_value(v) for k, v in data.items()
                                }
                                await client.publish(
                                    self.state_topic, json.dumps(state)
                                )
                                self.logger.debug(
                                    "State published: %d fields", len(state)
                                )
                            else:
                                # Mark offline after 5 consecutive failures
                                if self._consecutive_failures >= 5:
                                    await client.publish(
                                        self.avail_topic, "offline", retain=True
                                    )

                            await asyncio.sleep(interval)

                    async def command_loop() -> None:
                        async for message in client.messages:
                            topic = str(message.topic)
                            payload = message.payload.decode()
                            # Restore online status after a command
                            await client.publish(
                                self.avail_topic, "online", retain=True
                            )
                            await self.handle_command(topic, payload)

                    await asyncio.gather(poll_loop(), command_loop())

            except aiomqtt.MqttError as exc:
                self.logger.error("MQTT error: %s — reconnecting in 10s", exc)
                await asyncio.sleep(10)
            except Exception as exc:
                self.logger.error("Unexpected error: %s — restarting in 10s", exc)
                await asyncio.sleep(10)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────


def main() -> None:
    config = load_config()
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    bridge = BluettiBridge(config)
    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
