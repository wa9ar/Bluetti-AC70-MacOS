#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Scan Bluetooth to find your AC70's address
# Usage: bash scan.sh
# ─────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "❌ Virtual environment not found. Run first: bash install.sh"
    exit 1
fi

echo ""
echo "🔍 Scanning Bluetooth (10 seconds)..."
echo "   Make sure your AC70 is powered on and nearby."
echo ""

"$VENV_DIR/bin/python" - <<'EOF'
import asyncio
from bleak import BleakScanner

async def scan():
    devices = await BleakScanner.discover(timeout=10)
    bluetti_found = []
    for d in devices:
        name = d.name or ""
        if any(name.startswith(prefix) for prefix in [
            "AC1", "AC2", "AC3", "AC5", "AC6", "AC7", "AC8",
            "AP3", "EB3A", "EP", "EL", "Handsfree"
        ]):
            bluetti_found.append(d)

    if not bluetti_found:
        print("❌ No Bluetti device detected.")
        print("   Check that macOS Bluetooth is enabled and the AC70 is powered on.")
        return

    print("✅ Bluetti device(s) found:\n")
    for d in bluetti_found:
        print(f"   Name    : {d.name}")
        print(f"   Address : {d.address}")
        rssi = getattr(d, "rssi", None) or getattr(d, "advertisement", None) and d.advertisement.rssi
        if rssi:
            print(f"   RSSI    : {rssi} dBm")
        print()

    print("👉 Copy the address above into config.yaml (field 'address')")

asyncio.run(scan())
EOF
