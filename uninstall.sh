#!/usr/bin/env bash
# Remove linuXMouseButtonControl (lxmbc) from the system. This folder is left untouched.
# Run from this folder:
#   sudo bash uninstall.sh           remove the program, keep /etc/lxmbc.conf for a later reinstall
#   sudo bash uninstall.sh --purge   also delete /etc/lxmbc.conf and its backup
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo:  sudo bash uninstall.sh"; exit 1; }
systemctl disable --now lxmbc 2>/dev/null || true
rm -f /usr/local/bin/lxmbc /etc/systemd/system/lxmbc.service
systemctl daemon-reload
if [ "${1:-}" = "--purge" ]; then
  rm -f /etc/lxmbc.conf /etc/lxmbc.conf.bak
  echo "lxmbc and its config removed. Your mouse is back to normal."
else
  echo "lxmbc removed (config kept at /etc/lxmbc.conf). Your mouse is back to normal."
fi