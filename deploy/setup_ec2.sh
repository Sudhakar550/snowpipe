#!/usr/bin/env bash
# =============================================================================
# Dialog upload portal - one-time setup on an AWS EC2 instance (Ubuntu 24.04)
#
# Run on the EC2 instance, from the unzipped upload_app folder:
#     sudo bash deploy/setup_ec2.sh
#
# It will:
#   1. install Python, Caddy (web server with free automatic HTTPS) and tools
#   2. copy the app to /opt/dialog-upload, with its own Python environment
#   3. install Snowflake CLI in the same environment (for the later Snowflake step)
#   4. keep uploaded files in /data/dialog_uploads
#   5. run the app as a service (starts on boot, restarts if it stops)
#   6. publish it on https://<IP or domain> through Caddy (HTTPS)
#
# Safe to run again (for example after copying a new version of the app).
# =============================================================================
set -euo pipefail

APP_DIR=/opt/dialog-upload
DATA_DIR=/data/dialog_uploads
ENV_FILE=/etc/dialog-upload.env
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash deploy/setup_ec2.sh"; exit 1; fi
if [[ ! -f "$SRC_DIR/app.py" ]]; then echo "app.py not found in $SRC_DIR"; exit 1; fi

# ---------------------------------------------------------------- address
# Two choices:
#   * the server's IP address (default): works everywhere, also behind office firewalls that
#     block sslip.io. The browser shows a "Not secure" warning once (self-made certificate);
#     the connection is still encrypted.
#   * a real domain pointed at the Elastic IP (e.g. dialog-upload.thbs.com): trusted
#     certificate, no warning.
# On a re-run the address used last time is the default, so pressing Enter keeps it.
TOKEN=$(curl -s -m 3 -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
PUBLIC_IP=$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
[[ -z "$PUBLIC_IP" ]] && PUBLIC_IP=$(curl -s -m 5 http://checkip.amazonaws.com || true)
LAST_HOST=$(sed -n 's/^# dialog-upload address: //p' /etc/caddy/Caddyfile 2>/dev/null | head -1)
DEFAULT_HOST=${LAST_HOST:-$PUBLIC_IP}
echo
echo "Public IP of this server: ${PUBLIC_IP:-unknown}"
read -r -p "Web address [${DEFAULT_HOST}] (Enter = keep, or type a domain e.g. dialog-upload.thbs.com): " SITE_HOST
SITE_HOST=${SITE_HOST:-$DEFAULT_HOST}
if [[ -z "$SITE_HOST" ]]; then echo "No address. Attach an Elastic IP first, then run again."; exit 1; fi
if [[ ! "$SITE_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then echo "That is not a valid address: $SITE_HOST"; exit 1; fi
IS_IP=0; [[ "$SITE_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && IS_IP=1

# ---------------------------------------------------------------- logins
if [[ ! -f "$ENV_FILE" ]]; then
  read -r -p "Upload login name [dialog]: " U; U=${U:-dialog}
  read -r -s -p "Password for $U (min 10 characters): " P; echo
  if [[ ${#P} -lt 10 ]]; then echo "Password too short."; exit 1; fi
  if [[ "$P" == *","* || "$P" == *":"* ]]; then echo "Password may not contain , or :"; exit 1; fi
  umask 077
  cat > "$ENV_FILE" <<EOF
# Logins for the upload page: user:password,user2:password2   (edit, then: sudo systemctl restart dialog-upload)
UPLOAD_USERS=${U}:${P}
DATA_DIR=${DATA_DIR}
HOST=127.0.0.1
PORT=8000
EOF
  umask 022
  echo "Logins saved in $ENV_FILE"
else
  echo "Keeping existing logins in $ENV_FILE"
fi

# ---------------------------------------------------------------- packages
echo "Installing packages (a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq python3 python3-venv python3-pip curl debian-keyring debian-archive-keyring apt-transport-https gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y -qq
  apt-get install -y -qq caddy
fi

# ---------------------------------------------------------------- app
id -u dialogapp >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin dialogapp
mkdir -p "$APP_DIR" "$DATA_DIR"
cp -r "$SRC_DIR"/app.py "$SRC_DIR"/file_map.json "$SRC_DIR"/requirements.txt "$SRC_DIR"/static "$APP_DIR"/
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" snowflake-cli
ln -sf "$APP_DIR/venv/bin/snow" /usr/local/bin/snow
chown -R dialogapp:dialogapp "$APP_DIR" "$DATA_DIR"
chmod 640 "$ENV_FILE"; chown root:dialogapp "$ENV_FILE"

cat > /etc/systemd/system/dialog-upload.service <<EOF
[Unit]
Description=Dialog data upload portal
After=network-online.target

[Service]
User=dialogapp
Group=dialogapp
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ---------------------------------------------------------------- HTTPS
if [[ $IS_IP -eq 1 ]]; then
  # IP address: self-made certificate. Browsers send no name for an IP, so default_sni
  # tells Caddy which certificate to use.
  { echo "# dialog-upload address: ${SITE_HOST}"
    echo "{"
    echo "    default_sni ${SITE_HOST}"
    echo "}"
    echo
    echo "${SITE_HOST} {"
    echo "    tls internal"
  } > /etc/caddy/Caddyfile
else
  # Real domain: Caddy gets a free trusted certificate by itself.
  { echo "# dialog-upload address: ${SITE_HOST}"
    echo "${SITE_HOST} {"
  } > /etc/caddy/Caddyfile
fi
cat >> /etc/caddy/Caddyfile <<'CADDY'
    encode gzip
    reverse_proxy 127.0.0.1:8000 {
        transport http {
            read_timeout 30m
            write_timeout 30m
        }
    }
}
CADDY
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null

systemctl daemon-reload
systemctl enable --now dialog-upload
systemctl restart dialog-upload
systemctl enable caddy
systemctl restart caddy
sleep 3

echo
if systemctl is-active --quiet dialog-upload && systemctl is-active --quiet caddy; then
  echo "======================================================================"
  echo " DONE. Upload page:  https://${SITE_HOST}"
  if [[ $IS_IP -eq 1 ]]; then
    echo " (browser warning the first time: Advanced -> Continue. The connection is encrypted.)"
  else
    echo " (the certificate can take up to a minute the first time)"
  fi
  echo " Files are kept in:  ${DATA_DIR}/incoming/"
  echo " Upload log:         ${DATA_DIR}/upload_log.csv"
  echo " Logins:             ${ENV_FILE}"
  echo " Snowflake CLI:      snow --version"
  echo "======================================================================"
else
  echo "Something did not start. See:  sudo journalctl -u dialog-upload -n 50   and   sudo journalctl -u caddy -n 50"
  exit 1
fi
