#!/usr/bin/env bash
# Install or update linuXMouseButtonControl (lxmbc).  Run from this folder:  sudo bash install.sh
# Safe to re-run after editing lxmbc.py: it reinstalls and restarts the service if it is running.
# Your /etc/lxmbc.conf is kept; add --reset-config to replace it with this folder's defaults.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo:  sudo bash install.sh"; exit 1; }

# Clean up the earlier prototype name if it was ever installed.
if [ -e /etc/systemd/system/mousechord.service ] || [ -e /usr/local/bin/mousechord ]; then
  systemctl disable --now mousechord 2>/dev/null || true
  rm -f /usr/local/bin/mousechord /etc/mousechord.conf /etc/systemd/system/mousechord.service
  echo "Removed the old 'mousechord' prototype."
fi

python3 -c "import evdev" 2>/dev/null || apt-get install -y python3-evdev

if ! PYTHONDONTWRITEBYTECODE=1 python3 test_lxmbc.py >/dev/null 2>&1; then
  echo "Tests failed, nothing installed. See details with:  python3 test_lxmbc.py"
  exit 1
fi

install -m 755 lxmbc.py /usr/local/bin/lxmbc
install -m 644 lxmbc.service /etc/systemd/system/lxmbc.service
if [ "${1:-}" = "--reset-config" ] && [ -e /etc/lxmbc.conf ]; then
  cp /etc/lxmbc.conf /etc/lxmbc.conf.bak
  install -m 644 lxmbc.conf /etc/lxmbc.conf
  echo "Replaced /etc/lxmbc.conf with the defaults (old one saved as /etc/lxmbc.conf.bak)."
elif [ -e /etc/lxmbc.conf ]; then
  echo "Kept your existing /etc/lxmbc.conf (your tuning is preserved)."
  echo "To take the new defaults instead:  sudo bash install.sh --reset-config"
else
  install -m 644 lxmbc.conf /etc/lxmbc.conf
fi
systemctl daemon-reload

if systemctl is-active --quiet lxmbc; then
  systemctl restart lxmbc
  echo "lxmbc updated and restarted."
else
  echo "lxmbc installed (tests passed)."
  echo "Next:  sudo lxmbc --list          (check your mouse is detected)"
  echo "       sudo lxmbc --debug         (try it; Ctrl+C gives the mouse back)"
  echo "       sudo systemctl enable --now lxmbc   (run it at every boot)"
fi
