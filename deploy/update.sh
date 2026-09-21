#!/usr/bin/env bash
# Update installed PAZME safely; original DB, domain, TLS and Wilma are untouched.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'Run: sudo bash deploy/update.sh'; exit 1; }
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f /opt/pazme/app.py ] || { echo 'Old PAZME app is not installed. Stop.'; exit 1; }
[ -f /var/lib/pazme/pazme.sqlite3 ] || { echo 'Live PAZME database missing. Stop.'; exit 1; }
BACKUP_DIR=/root/pazme-backups
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%d-%H%M%S)
tar -czf "$BACKUP_DIR/pazme-app-before-$STAMP.tar.gz" -C /opt pazme
chmod 600 "$BACKUP_DIR/pazme-app-before-$STAMP.tar.gz"
# SQLite online backup is consistent even when site is serving requests.
python3 - "$BACKUP_DIR/pazme-db-before-$STAMP.sqlite3" <<'PYBACK'
import sqlite3,sys
source=sqlite3.connect('/var/lib/pazme/pazme.sqlite3')
target=sqlite3.connect(sys.argv[1])
with target:source.backup(target)
target.close();source.close()
PYBACK
chmod 600 "$BACKUP_DIR/pazme-db-before-$STAMP.sqlite3"
# Secret generated only on the actual server, never uploaded to GitHub.
if [ ! -f /var/lib/pazme/admin_credentials.json ]; then
    python3 - <<'PYSECRET'
import hashlib, json, os, pathlib, secrets
password=secrets.token_urlsafe(18)
salt=os.urandom(16)
digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,200000).hex()
path=pathlib.Path('/var/lib/pazme/admin_credentials.json')
path.write_text(json.dumps({'salt':salt.hex(),'hash':digest}))
os.chown(path, __import__('pwd').getpwnam('pazme').pw_uid, __import__('grp').getgrnam('pazme').gr_gid)
os.chmod(path,0o600)
secret=pathlib.Path('/root/PAZME_ADMIN_PASSWORD.txt')
secret.write_text('PAZME: https://pazme.ru/admin/\nЛогин: admin\nПароль: '+password+'\n')
os.chmod(secret,0o600)
PYSECRET
fi
for f in app.py manage.py index.html participant.html business.html privacy.html consent.html favicon.svg robots.txt requirements.txt; do
  install -m 0644 "$SOURCE_DIR/$f" "/opt/pazme/$f"
done
for f in style.css app.js profile.js sticker.webp qr-site.png smile-partner.webp smile-business.webp wordmark.png; do
  install -m 0644 "$SOURCE_DIR/assets/$f" "/opt/pazme/assets/$f"
done
/opt/pazme/.venv/bin/python -m py_compile /opt/pazme/app.py /opt/pazme/manage.py
# Initialize non-destructive migration, including new profile fields.
(cd /opt/pazme && sudo -u pazme env PAZME_DB=/var/lib/pazme/pazme.sqlite3 /opt/pazme/.venv/bin/python -c 'from app import connection; connection().close()') || { echo 'Migration failed, use the backup; stop.'; exit 1; }
systemctl restart pazme
systemctl is-active --quiet pazme || { echo 'Site did not start; use the backup and send log output.'; exit 1; }
code=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/qr/ || true)
[ "$code" = 200 ] || { echo "Unexpected HTTP code: $code; stop."; exit 1; }
code=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/business/ || true)
[ "$code" = 200 ] || { echo "Business page HTTP code: $code; stop."; exit 1; }
echo 'PAZME updated. Database and analytics preserved. HTTPS and Nginx unchanged.'
echo 'Admin: https://pazme.ru/admin/ (credentials: /root/PAZME_ADMIN_PASSWORD.txt)'
echo "App backup: $BACKUP_DIR/pazme-app-before-$STAMP.tar.gz"
echo "DB backup:  $BACKUP_DIR/pazme-db-before-$STAMP.sqlite3"
