#!/usr/bin/env bash
# =============================================================================
# Dialog upload portal - install a new version of the code on the server.
#
#     cd ~/snowpipe && git pull
#     sudo bash deploy/update.sh
#
# Copies the app (page, server, file list) and the pipeline to /opt/dialog-upload,
# installs any new Python packages, restarts both services and shows their status.
# Keeps logins, the Snowflake key and settings, the web address and all uploaded files.
# =============================================================================
set -euo pipefail

APP_DIR=/opt/dialog-upload
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash deploy/update.sh"; exit 1; fi
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then echo "Not installed yet. Run: sudo bash deploy/setup_ec2.sh"; exit 1; fi

echo "Copying the app..."
cp -r "$SRC_DIR"/app.py "$SRC_DIR"/file_map.json "$SRC_DIR"/requirements.txt "$SRC_DIR"/static "$APP_DIR"/
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ -f /etc/systemd/system/dialog-pipeline.service ]]; then
  echo "Copying the pipeline..."
  mkdir -p "$APP_DIR/pipeline"
  cp "$SRC_DIR/pipeline/pusher.py" "$APP_DIR/pipeline/"
fi
chown -R dialogapp:dialogapp "$APP_DIR"

systemctl restart dialog-upload
[[ -f /etc/systemd/system/dialog-pipeline.service ]] && systemctl restart dialog-pipeline
sleep 2

echo
for s in dialog-upload dialog-pipeline caddy; do
  if systemctl list-unit-files "$s.service" >/dev/null 2>&1 && systemctl cat "$s" >/dev/null 2>&1; then
    printf "  %-16s %s\n" "$s" "$(systemctl is-active "$s" || true)"
  fi
done
ADDR=$(sed -n 's/^# dialog-upload address: //p' /etc/caddy/Caddyfile 2>/dev/null | head -1)
echo
echo "Updated. Open https://${ADDR:-<server address>} and press Ctrl+F5 to load the new page."
