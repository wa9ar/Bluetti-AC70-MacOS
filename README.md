# Bluetti AC70 — Home Assistant Bridge (macOS + Docker)

A Python bridge that connects a **Bluetti AC70 power station** to **Home Assistant** via MQTT,
running natively on macOS where Bluetooth is accessible — bypassing the Docker BLE limitation.

## Why this approach?

When Home Assistant runs in a **Docker container on macOS**, the Linux VM used by Docker Desktop
has no access to the host's Bluetooth hardware. This bridge runs directly on the Mac host,
reads the AC70 via BLE, and publishes data to an MQTT broker reachable by HA.

```
AC70 (BLE)
    ↕ Bluetooth  (native macOS — no Docker limitation)
Python Bridge  ←── runs on Mac host
    ↕ MQTT port 1883
Mosquitto (Docker container)
    ↕ internal Docker network
Home Assistant (Docker container)
```

## Features

- **16 sensors** — battery %, time remaining, AC/DC power, voltages, currents, frequencies, BMS version
- **5 switches** — AC output, DC output, ECO AC, ECO DC, Power Lifting
- **3 selects** — Charging Mode (Standard / Silent / Turbo), ECO AC/DC Time Mode
- **Auto-discovery** — entities appear in HA automatically via MQTT discovery, no YAML needed
- **Auto-detects BLE encryption** — works with both encrypted and unencrypted AC70 firmware
- **macOS service** — starts at login, auto-restarts on crash via launchd (`KeepAlive: true`)

---

## Requirements

- macOS with Bluetooth (built-in or USB adapter)
- Python 3.11+
- Home Assistant running in Docker
- An MQTT broker (Mosquitto) accessible on port 1883 from the Mac host

---

## Step-by-step setup

### Step 1 — Add Mosquitto to your Docker Compose

If you already have an MQTT broker (e.g. used by Zigbee2MQTT), skip this step.

Add the following service to your `docker-compose.yml`:

```yaml
mosquitto:
  image: eclipse-mosquitto:2
  container_name: mosquitto
  restart: unless-stopped
  ports:
    - "1883:1883"
  volumes:
    - ./config/mosquitto/config:/mosquitto/config
    - ./config/mosquitto/data:/mosquitto/data
    - ./config/mosquitto/log:/mosquitto/log
  networks:
    - ha-network
```

Create the config file at `./config/mosquitto/config/mosquitto.conf`:

```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest file /mosquitto/log/mosquitto.log
```

Then start it:

```bash
docker compose up -d mosquitto
```

### Step 2 — Enable MQTT integration in Home Assistant

Go to **Settings → Integrations → Add Integration → MQTT**

Enter your broker details:
- Host: `mosquitto` (Docker service name) or `localhost`
- Port: `1883`
- Username / Password: as configured in your Mosquitto config

### Step 3 — Clone this repo

```bash
git clone https://github.com/wa9ar/Bluetti-AC70-MacOS.git
cd Bluetti-AC70-MacOS
```

### Step 4 — Scan for your AC70's Bluetooth address

Make sure your AC70 is powered on, then run:

```bash
bash scan.sh
```

You will get output like:

```
✅ Bluetti device(s) found:

   Name    : AC70XXXXXXXXXXXX
   Address : XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
```

Copy both the **Name** and **Address** values.

### Step 5 — Edit config.yaml

```yaml
mqtt:
  host: "localhost"
  port: 1883
  username: "your_mqtt_user"      # or leave empty if anonymous
  password: "your_mqtt_password"  # or leave empty if anonymous

bluetti:
  address: "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"   # from scan.sh
  name: "AC70XXXXXXXXXXXX"                           # from scan.sh
  polling_interval: 30
  polling_timeout: 15
```

### Step 6 — Install and start the bridge

```bash
bash install.sh
```

This script will:
1. Check Python 3 is available
2. Create a Python virtual environment (`.venv/`)
3. Install all required packages from `requirements.txt`
4. Generate the launchd service with correct paths
5. Register and start the service — it will now run at every login automatically

### Step 7 — Check it works

```bash
tail -f logs/bridge.log
```

You should see:

```
[INFO] BluettiBridge: Encryption detected: True
[INFO] BluettiBridge: Discovery published: 16 sensors, 5 switches, 3 selects
[INFO] BluettiBridge: MQTT connected, device online
```

Then in Home Assistant → **Settings → Devices** — a **Bluetti AC70** device will appear
with all entities ready to use.

---

## Useful commands

```bash
# Live logs
tail -f logs/bridge.log

# Error logs
tail -f logs/bridge.error.log

# Stop the service
launchctl unload ~/Library/LaunchAgents/com.bluetti.bridge.plist

# Restart the service
launchctl unload ~/Library/LaunchAgents/com.bluetti.bridge.plist \
  && launchctl load ~/Library/LaunchAgents/com.bluetti.bridge.plist
```

> **Auto-restart on reboot**: The bridge is registered as a launchd agent and starts automatically
> at user login. Enable **Auto Login** in *System Settings → General → Login* to start it
> after a reboot without manual login.

---

## Project structure

```
.
├── bluetti_bridge.py          # Main bridge — BLE polling + MQTT publish/subscribe
├── config.yaml                # Configuration template (MQTT + BLE settings)
├── requirements.txt           # Python dependencies
├── install.sh                 # One-command install + launchd service registration
├── scan.sh                    # BLE scanner — finds your AC70's UUID address
└── com.bluetti.bridge.plist   # macOS launchd service definition (template)
```

---

## Credits

- **[bluetti-bt-lib](https://github.com/Patrick762/bluetti-bt-lib)** by [@Patrick762](https://github.com/Patrick762) —
  the BLE communication library this bridge is entirely built on.
  All device protocol handling, BLE encryption, and register parsing come from this library.

- **[hassio-bluetti-bt](https://github.com/Patrick762/hassio-bluetti-bt)** by [@Patrick762](https://github.com/Patrick762) —
  the original Home Assistant integration that inspired the entity structure and MQTT discovery design.

---

## Built with

This project was developed with the assistance of **[Claude](https://claude.ai)** by Anthropic —
used for architecture design, code generation, and debugging the BLE + MQTT integration on macOS.

---

## License

MIT — see [LICENSE](LICENSE)
