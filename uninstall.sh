#!/usr/bin/env bash
# Remove linuXMouseButtonControl (lxmbc) from the system. This folder is left untouched.
# Run from this folder:  sudo bash uninstall.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo:  sudo bash uninstall.sh"; exit 1; }
systemctl disable --now lxmbc 2>/dev/null || true
rm -f /usr/local/bin/lxmbc /etc/lxmbc.conf /etc/systemd/system/lxmbc.service
systemctl daemon-reload
echo "lxmbc removed. Your mouse is back to normal."
