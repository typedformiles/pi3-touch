#!/bin/bash
# Pi3 Touch installer. Run ON the Pi as root from a copy of this repo (mac/deploy.sh does it):
#   sudo bash ~/pi3-touch/pi/install.sh
# Safe to re-run. Installs to /opt/pi3-touch, sets up the launcher + app services, enables the
# HyperPixel, and migrates the older booth-only setup (showloop/showtouch) if it's there.
# Options (env): PI_USER=tim  SKIP_APT=1
set -e
trap 'echo "INSTALL FAILED (line $LINENO)"' ERR
REPO=$(cd "$(dirname "$0")/.." && pwd)
DEST=/opt/pi3-touch
USER_NAME=${PI_USER:-${SUDO_USER:-tim}}
CFG=/boot/firmware/config.txt
REBOOT=0
step() { echo; echo "===== $*"; }

[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
id "$USER_NAME" >/dev/null || { echo "no such user: $USER_NAME (set PI_USER)"; exit 1; }

step "packages"
PKGS="mpv python3 python3-pygame python3-pil libgbm1 libegl1"
installed() { dpkg -s "$1" >/dev/null 2>&1; }
MISSING=$(for p in $PKGS; do
  installed "$p" || echo "$p"
done)
if [ -z "$MISSING" ]; then
  echo "all installed"
elif [ -n "$SKIP_APT" ]; then
  echo "SKIP_APT set - not installing: $MISSING"
else
  apt-get update -q
  # --no-install-recommends: mpv's recommends alone pull in ~190 MB on Lite
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends $MISSING
fi

step "user groups"
usermod -aG video,render,input "$USER_NAME" && id -nG "$USER_NAME"

step "HyperPixel display"
if grep -q '^dtoverlay=vc4-kms-dpi-hyperpixel4' $CFG; then
  echo "already enabled"
else
  [ -e $CFG.pre-pi3-touch ] || cp $CFG $CFG.pre-pi3-touch
  printf '\n[all]\n# HyperPixel 4.0 rectangular touch (Pi3 Touch)\ndtoverlay=vc4-kms-dpi-hyperpixel4\n' >> $CFG
  echo "added - reboot needed"; REBOOT=1
fi

step "migrate older booth-only setup"
if [ -e /etc/systemd/system/showloop.service ] || [ -e /etc/systemd/system/showtouch.service ]; then
  systemctl disable --now showtouch.service showloop.service 2>/dev/null || true
  rm -rf /etc/systemd/system/showloop.service /etc/systemd/system/showloop.service.d \
         /etc/systemd/system/showtouch.service /etc/udev/rules.d/99-showloop-hdmi.rules \
         /usr/local/bin/showloop* /usr/local/bin/showtouch* /usr/local/share/showloop
  mkdir -p /var/lib/pi3-touch
  [ -e /var/lib/pi3-touch/last-app ] || echo booth-display > /var/lib/pi3-touch/last-app
  echo "removed showloop/showtouch; booth display set as last app (slides in /boot/firmware/show kept)"
else
  echo "nothing to migrate"
fi

step "app files -> $DEST"
rm -rf $DEST.new && mkdir -p $DEST.new
cp -a "$REPO"/common "$REPO"/launcher "$REPO"/apps "$REPO"/pi $DEST.new/
find $DEST.new \( -name '*.png' -o -name '__pycache__' -o -name '.DS_Store' \) -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf $DEST.old; [ -e $DEST ] && mv $DEST $DEST.old; mv $DEST.new $DEST; rm -rf $DEST.old
chmod 755 $DEST/apps/booth-display/showloop $DEST/pi/bin/*
mkdir -p /boot/firmware/show
du -sh $DEST

step "systemd units"
for f in "$REPO"/pi/systemd/*.service "$REPO"/pi/systemd/*.target "$REPO"/pi/systemd/*.path; do
  sed "s/@USER@/$USER_NAME/g" "$f" > /etc/systemd/system/$(basename "$f")
done
# Only one app owns the screens at a time: each app's target stops any other running app,
# however it was started (the launcher's own Conflicts= only covers going via the menu)
rm -f /etc/systemd/system/pi3-app-*.target.d/one-app.conf
TARGETS=$(cd "$REPO"/pi/systemd && ls pi3-app-*.target)
for t in $TARGETS; do
  mkdir -p /etc/systemd/system/$t.d
  printf '[Unit]\n# Written by install.sh\nConflicts=%s\n' "$(echo $TARGETS | tr ' ' '\n' | grep -vx "$t" | tr '\n' ' ')" \
    > /etc/systemd/system/$t.d/one-app.conf
done
sed "s/@USER@/$USER_NAME/g" "$REPO"/pi/systemd/pi3-touch.tmpfiles > /etc/tmpfiles.d/pi3-touch.conf
systemd-tmpfiles --create /etc/tmpfiles.d/pi3-touch.conf
cp "$REPO"/pi/udev/*.rules /etc/udev/rules.d/ && udevadm control --reload
systemctl daemon-reload
systemctl enable pi3-launcher.service pi3-home.path
systemctl start pi3-home.path
ls /etc/systemd/system/pi3-*

step "start"
if [ $REBOOT = 1 ]; then
  echo "rebooting in 5 s to enable the HyperPixel"
  sync; systemd-run --on-active=5 --unit=pi3-touch-reboot /usr/bin/systemctl reboot
else
  systemctl restart pi3-launcher.service
  echo "launcher restarted (countdown to: $(cat /var/lib/pi3-touch/last-app 2>/dev/null || echo none))"
fi
echo "INSTALL OK"
