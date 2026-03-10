#!/bin/bash
# Moode Display - Setup Script
# Run this on the Pi 3B after flashing Raspberry Pi OS Lite (32-bit, Bookworm)
# Usage: bash setup.sh

set -e

echo "=== Moode Display Setup ==="

# 1. System update
echo "[1/6] Updating system..."
sudo apt-get update -q
sudo apt-get upgrade -y -q

# 2. Install HyperPixel 4.0 driver
echo "[2/6] Installing HyperPixel driver..."
if ! grep -q "hyperpixel4" /boot/config.txt 2>/dev/null; then
    curl -sSL get.pimoroni.com/hyperpixel4-legacy | bash
    echo "HyperPixel driver installed. Reboot required after setup."
else
    echo "HyperPixel driver already installed."
fi

# 3. Install Python dependencies
echo "[3/6] Installing Python packages..."
sudo apt-get install -y -q \
    python3-pygame \
    python3-pil \
    python3-mpd \
    python3-requests \
    git

# 4. Copy app files
echo "[4/6] Installing app..."
mkdir -p "$HOME/moode_display"
cp moode_display.py "$HOME/moode_display/"
chmod +x "$HOME/moode_display/moode_display.py"

# 5. Configure auto-login to console
echo "[5/6] Configuring auto-login..."
sudo raspi-config nonint do_boot_behaviour B2  # Console autologin

# 6. Install and enable systemd service
echo "[6/6] Installing systemd service..."
sudo cp moode-display.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable moode-display.service

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Reboot to apply HyperPixel driver: sudo reboot"
echo "  2. After reboot, start the service:   sudo systemctl start moode-display"
echo "  3. Check logs if needed:              journalctl -u moode-display -f"
echo ""
echo "To edit config (MPD host, colours, layout):"
echo "  nano "$HOME/moode_display/moode_display.py"
echo "  (Edit the # Config section at the top)"
