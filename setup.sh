#!/bin/bash
# Moode Display - Setup Script
# Run on your Pi after flashing Raspberry Pi OS Lite (32-bit, Bookworm)
# Usage: bash setup.sh

set -e

APP_DIR="$HOME/moode_display"

echo "=== Moode Display Setup ==="
echo "User: $USER | Home: $HOME"
echo ""

# 1. System update
echo "[1/6] Updating system..."
sudo apt-get update -q
sudo apt-get upgrade -y -q

# 2. Install HyperPixel 4.0 driver (built into Bookworm kernel)
echo "[2/6] Configuring HyperPixel 4.0 display..."
CONFIG="/boot/firmware/config.txt"
if ! grep -q "vc4-kms-dpi-hyperpixel4" "$CONFIG" 2>/dev/null; then
    echo "" | sudo tee -a "$CONFIG"
    echo "# HyperPixel 4.0 Rectangular Touch" | sudo tee -a "$CONFIG"
    echo "dtoverlay=vc4-kms-dpi-hyperpixel4" | sudo tee -a "$CONFIG"
    echo "HyperPixel overlay added. Reboot required after setup."
else
    echo "HyperPixel overlay already configured."
fi

# 3. Install Python dependencies + EGL libs for kmsdrm
echo "[3/6] Installing dependencies..."
sudo apt-get install -y -q \
    python3-pygame \
    python3-pil \
    python3-mpd \
    python3-requests \
    libegl-dev \
    libgbm1

# 4. Copy app files
echo "[4/6] Installing app to $APP_DIR..."
mkdir -p "$APP_DIR"
cp moode_display.py "$APP_DIR/"
chmod +x "$APP_DIR/moode_display.py"

# 5. Add user to video/render/input groups for KMS/DRM + touch access
echo "[5/6] Setting up permissions..."
sudo usermod -aG video,render,input "$USER"

# 6. Generate and install systemd service (uses current user/home)
echo "[6/6] Installing systemd service..."
sudo tee /etc/systemd/system/moode-display.service > /dev/null <<EOF
[Unit]
Description=Moode Remote Display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_KMSDRM_DEVICE_INDEX=0
ExecStart=/usr/bin/python3 $APP_DIR/moode_display.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

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
echo "  Then edit SDL_KMSDRM_DEVICE_INDEX:"
echo "  sudo systemctl edit moode-display  (add Environment=SDL_KMSDRM_DEVICE_INDEX=1)"
echo ""
echo "To edit config (MPD host, colours, layout):"
echo "  nano $APP_DIR/moode_display.py"
echo "  (Edit the # Config section at the top)"
