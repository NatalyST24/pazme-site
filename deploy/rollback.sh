#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'Run: sudo bash deploy/rollback.sh'; exit 1; }
latest=$(find /root/pazme-backups -maxdepth 1 -name 'pazme-app-before-*.tar.gz' -type f | sort | tail -1)
[ -n "$latest" ] || { echo 'App backup not found'; exit 1; }
tar -xzf "$latest" -C /opt
systemctl restart pazme
systemctl is-active --quiet pazme
printf 'Previous PAZME code restored from: %s\nDatabase, phones and statistics have NOT been erased.\n' "$latest"
