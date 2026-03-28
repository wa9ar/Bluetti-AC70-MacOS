#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Bluetti Bridge — macOS install script
# Usage: bash install.sh
# ─────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PLIST_SRC="$SCRIPT_DIR/com.bluetti.bridge.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.bluetti.bridge.plist"
LOGS_DIR="$SCRIPT_DIR/logs"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       Bluetti Bridge — macOS Installer       ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Check Python 3 ─────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found. Install it with: brew install python"
    exit 1
fi
PYTHON=$(command -v python3)
echo "✅ Python: $($PYTHON --version)"

# ── 2. Create virtual environment ─────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
echo "✅ Virtual environment: $VENV_DIR"

# ── 3. Install dependencies ───────────────────────────
echo "⬇️  Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "✅ Dependencies installed"

# ── 4. Create logs directory ──────────────────────────
mkdir -p "$LOGS_DIR"

# ── 5. Check configuration ────────────────────────────
if grep -q "XXXXXXXX" "$SCRIPT_DIR/config.yaml"; then
    echo ""
    echo "⚠️  WARNING: AC70 Bluetooth address is not configured yet."
    echo "   Run first:  bash scan.sh"
    echo "   Then edit   config.yaml  and replace the XXXXXXXX address"
    echo ""
fi

# ── 6. Generate and register launchd service ──────────
echo "🔧 Configuring macOS service..."
sed \
    -e "s|VENV_PATH|$VENV_DIR|g" \
    -e "s|SCRIPT_PATH|$SCRIPT_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Unload if already running
if launchctl list | grep -q "com.bluetti.bridge"; then
    echo "♻️  Reloading service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

launchctl load "$PLIST_DST"
echo "✅ Service registered and started"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║           Installation complete!             ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "📋 Useful commands:"
echo "   Live logs   : tail -f $LOGS_DIR/bridge.log"
echo "   Error logs  : tail -f $LOGS_DIR/bridge.error.log"
echo "   Stop        : launchctl unload $PLIST_DST"
echo "   Restart     : launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
echo ""
