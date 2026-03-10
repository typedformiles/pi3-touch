#!/bin/bash
# Moode Display - Setup Script
# Run this on the Pi 3B after flashing Raspberry Pi OS Lite (32-bit, Bookworm)
# Username: tim, Hostname: mooderemote
# Usage: bash setup.sh

set -e

echo "=== Moode Display Setup ==="

# 1. System update
echo "[1/6] Updating system..."
sudo apt-get update -q
sudo apt-get upgrade -y -q

# 2. Install HyperPixel 4.0 driver (built into Bookworm kernel)
echo "[2/6] Configuring HyperPixel 4.0 display..."
CONFIG="/boot/firmware/config.txt"
if ! grep -q "vc4-kms-dpi-hyperpixel4" "$CONFIG" 2>/dev/null; then
    # Add HyperPixel overlay (vc4-kms-v3d should already be present on Bookworm)
    echo "" | sudo tee -a "$CONFIG"
    echo "# HyperPixel 4.0 Rectangular Touch" | sudo tee -a "$CONFIG"
    echo "dtoverlay=vc4-kms-dpi-hyperpixel4" | sudo tee -a "$CONFIG"
    echo "HyperPixel overlay added. Reboot required after setup."
else
    echo "HyperPixel overlay already configured."
fi

# 3. Install Python dependencies + EGL libs for kmsdrm
echo "[3/6] Installing Python packages..."
sudo apt-get install -y -q \
    python3-pygame \
    python3-pil \
    python3-mpd2 \
    python3-requests \
    libegl-dev \
    libgbm1 \
    git

# 4. Copy app files
echo "[4/6] Installing app..."
mkdir -p "$HOME/moode_display"
cp moode_display.py "$HOME/moode_display/"
chmod +x "$HOME/moode_display/moode_display.py"

# 5. Add user to video/render/input groups for KMS/DRM + touch access
echo "[5/6] Setting up permissions..."
sudo usermod -aG video,render,input "$USER"

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
echo "  2. After reboot the service starts automatically."
echo "  3. Check logs if needed:              journalctl -u moode-display -f"
echo ""
echo "If the display doesn't appear, check which DRI device the HyperPixel uses:"
echo "  ls /dev/dri/"
echo "  Then edit SDL_KMSDRM_DEVICE_INDEX in moode-display.service (0 or 1)"
echo ""
echo "To edit config (MPD host, colours, layout):"
echo "  nano $HOME/moode_display/moode_display.py"
echo "  (Edit the # Config section at the top)"
