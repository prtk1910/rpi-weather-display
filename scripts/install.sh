#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo WEATHER_DISPLAY_PIN=... scripts/install.sh" >&2
  exit 1
fi
if [[ -z ${WEATHER_DISPLAY_PIN:-} ]]; then
  echo "WEATHER_DISPLAY_PIN is required and must not be stored in the repository." >&2
  exit 1
fi
if [[ ${#WEATHER_DISPLAY_PIN} -lt 4 || ${#WEATHER_DISPLAY_PIN} -gt 64 || $WEATHER_DISPLAY_PIN == *$'\n'* ]]; then
  echo "WEATHER_DISPLAY_PIN must be 4–64 characters with no newline." >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]] || ! grep -q 'VERSION_CODENAME=bookworm' /etc/os-release; then
  echo "Raspberry Pi OS Bookworm is the supported baseline." >&2
  exit 1
fi
if ! command -v Xorg >/dev/null && ! pgrep -x Xorg >/dev/null; then
  echo "X11 is required. Select X11 in raspi-config before installing." >&2
  exit 1
fi

SERVICE_USER=${SUDO_USER:-pi}
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service user '$SERVICE_USER' does not exist." >&2
  exit 1
fi
USER_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
INSTALL_DIR=/opt/weather-display

if command -v xrandr >/dev/null && pgrep -x Xorg >/dev/null; then
  MODE=$(sudo -u "$SERVICE_USER" DISPLAY=:0 XAUTHORITY="$USER_HOME/.Xauthority" xrandr --current 2>/dev/null | awk '/ connected / {for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+x[0-9]+\+/) {print $i; exit}}')
  if [[ -n ${MODE:-} && $MODE != 480x320* ]]; then
    echo "Display is '$MODE'; expected 480x320. Fix the screen configuration first." >&2
    exit 1
  fi
fi

apt-get update
apt-get install -y --no-install-recommends python3-venv python3-pip python3-pygame python3-flask python3-requests x11-xserver-utils avahi-daemon libnss-mdns
install -d -m 0755 "$INSTALL_DIR" /etc/weather-display
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 /var/lib/weather-display
cp -a "$SOURCE_DIR/weather_display" "$SOURCE_DIR/systemd" "$INSTALL_DIR/"
install -m 0644 "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/requirements.txt" \
  "$SOURCE_DIR/LICENSE" "$SOURCE_DIR/README.md" "$INSTALL_DIR/"
python3 -m venv --system-site-packages "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --disable-pip-version-check --no-deps --force-reinstall "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
umask 077
printf 'WEATHER_DISPLAY_PIN=%s\n' "$WEATHER_DISPLAY_PIN" > /etc/weather-display/environment
unset WEATHER_DISPLAY_PIN
sed -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@USER_HOME@|$USER_HOME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" "$INSTALL_DIR/systemd/weather-display.service.in" \
    > /etc/systemd/system/weather-display.service
chmod 0644 /etc/systemd/system/weather-display.service
systemctl daemon-reload
hostnamectl set-hostname weather-display
if grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
  sed -i -E 's/^(127\.0\.1\.1[[:space:]]+).*/\1weather-display/' /etc/hosts
else
  printf '127.0.1.1\tweather-display\n' >> /etc/hosts
fi
systemctl enable --now avahi-daemon.service
systemctl enable --now weather-display.service
echo "Installed. Open http://weather-display.local:8080 or use the Pi's IP address."
