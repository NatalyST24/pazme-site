#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then printf 'Run with sudo: sudo bash deploy/install.sh\n' >&2; exit 1; fi
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -e /etc/nginx/sites-available/pazme.ru ]; then printf "Existing Nginx config found: /etc/nginx/sites-available/pazme.ru. Review it manually.\n" >&2; exit 1; fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx sqlite3
if ! id pazme >/dev/null 2>&1; then useradd --system --home /var/lib/pazme --shell /usr/sbin/nologin pazme; fi
install -d -o pazme -g pazme -m 0700 /var/lib/pazme
install -d -o root -g root -m 0755 /opt/pazme /opt/pazme/assets
for f in app.py manage.py index.html privacy.html consent.html favicon.svg robots.txt requirements.txt; do install -m 0644 "$SOURCE_DIR/$f" "/opt/pazme/$f"; done
for f in style.css app.js sticker.webp; do install -m 0644 "$SOURCE_DIR/assets/$f" "/opt/pazme/assets/$f"; done
python3 -m venv /opt/pazme/.venv
/opt/pazme/.venv/bin/python -m pip install --disable-pip-version-check -r /opt/pazme/requirements.txt
install -m 0644 "$SOURCE_DIR/deploy/pazme.service" /etc/systemd/system/pazme.service
install -m 0644 "$SOURCE_DIR/deploy/pazme-purge.service" /etc/systemd/system/pazme-purge.service
install -m 0644 "$SOURCE_DIR/deploy/pazme-purge.timer" /etc/systemd/system/pazme-purge.timer
install -m 0644 "$SOURCE_DIR/deploy/nginx.conf" /etc/nginx/sites-available/pazme.ru
ln -s /etc/nginx/sites-available/pazme.ru /etc/nginx/sites-enabled/pazme.ru
nginx -t
systemctl daemon-reload
systemctl enable --now pazme.service pazme-purge.timer nginx.service
systemctl reload nginx
printf '\nPAZME installed. Check: curl -I http://127.0.0.1:8765/qr/\nSet DNS for pazme.ru before enabling HTTPS with Certbot.\n'
