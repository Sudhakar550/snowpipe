#!/usr/bin/env bash
# =============================================================================
# Dialog upload pipeline - step 2 setup on the EC2 server (run AFTER setup_ec2.sh)
#
#     sudo bash deploy/setup_pipeline.sh
#
# It will:
#   1. create a key pair for the Snowflake user DIALOG_PIPELINE (kept in /etc/dialog-pipeline)
#   2. print the public key and the SQL to paste in Snowsight (creates the user DIALOG_PIPELINE)
#   3. save the Snowflake settings in /etc/dialog-pipeline.env
#   4. test the login, then start the service "dialog-pipeline", which pushes every
#      uploaded file to Snowflake and loads it into RAW (rows added as is)
#
# Safe to run again: the key and settings are kept.
# =============================================================================
set -euo pipefail

APP_DIR=/opt/dialog-upload
KEY_DIR=/etc/dialog-pipeline
ENV_FILE=/etc/dialog-pipeline.env
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash deploy/setup_pipeline.sh"; exit 1; fi
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then echo "Run deploy/setup_ec2.sh first."; exit 1; fi
if [[ ! -f "$SRC_DIR/pipeline/pusher.py" ]]; then echo "pipeline/pusher.py not found in $SRC_DIR"; exit 1; fi

# ---------------------------------------------------------------- 1. key pair
mkdir -p "$KEY_DIR"
if [[ ! -f "$KEY_DIR/rsa_key.p8" ]]; then
  openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -nocrypt -out "$KEY_DIR/rsa_key.p8"
  openssl rsa -in "$KEY_DIR/rsa_key.p8" -pubout -out "$KEY_DIR/rsa_key.pub" 2>/dev/null
  echo "New key pair created in $KEY_DIR"
fi
chown -R root:dialogapp "$KEY_DIR"
chmod 750 "$KEY_DIR"; chmod 640 "$KEY_DIR/rsa_key.p8"; chmod 644 "$KEY_DIR/rsa_key.pub"
PUBKEY=$(grep -v -- "-----" "$KEY_DIR/rsa_key.pub" | tr -d '\n')

# ---------------------------------------------------------------- 2. settings
# An old settings file with a bad account value (e.g. text pasted by mistake) is replaced.
if [[ -f "$ENV_FILE" ]] && ! grep -qE '^SNOWFLAKE_ACCOUNT=[A-Za-z0-9._-]+$' "$ENV_FILE"; then
  echo "Settings in $ENV_FILE look wrong, asking again."; rm -f "$ENV_FILE"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  while true; do
    read -r -p "Snowflake account identifier [COUTGXW-TK77861]: " ACC; ACC=${ACC:-COUTGXW-TK77861}
    [[ "$ACC" =~ ^[A-Za-z0-9._-]+$ ]] && break
    echo "That does not look like an account identifier (letters, digits, - . _ only, e.g. COUTGXW-TK77861). Try again."
  done
  umask 077
  cat > "$ENV_FILE" <<EOF
# Snowflake login for the upload pipeline (edit, then: sudo systemctl restart dialog-pipeline)
SNOWFLAKE_ACCOUNT=${ACC}
SNOWFLAKE_USER=DIALOG_PIPELINE
SNOWFLAKE_PRIVATE_KEY_FILE=${KEY_DIR}/rsa_key.p8
SNOWFLAKE_ROLE=CR_DATA_ENGINEER
SNOWFLAKE_WAREHOUSE=CR_LOAD_WH
PIPELINE_INTERVAL=60
EOF
  umask 022
fi
chown root:dialogapp "$ENV_FILE"; chmod 640 "$ENV_FILE"

# ---------------------------------------------------------------- 3. code + service
mkdir -p "$APP_DIR/pipeline"
cp "$SRC_DIR/pipeline/pusher.py" "$APP_DIR/pipeline/"
"$APP_DIR/venv/bin/pip" install -q snowflake-connector-python cryptography
chown -R dialogapp:dialogapp "$APP_DIR/pipeline"

cat > /etc/systemd/system/dialog-pipeline.service <<EOF
[Unit]
Description=Dialog upload pipeline (push uploads to Snowflake and load RAW)
After=network-online.target dialog-upload.service

[Service]
User=dialogapp
Group=dialogapp
WorkingDirectory=${APP_DIR}/pipeline
EnvironmentFile=/etc/dialog-upload.env
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/pipeline/pusher.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

# ---------------------------------------------------------------- 4. Snowflake user + test
run_test() {
  sudo -u dialogapp bash -c "export DATA_DIR=/data/dialog_uploads; set -a; . ${ENV_FILE}; set +a; ${APP_DIR}/venv/bin/python ${APP_DIR}/pipeline/pusher.py --test"
}

if ! run_test 2>/dev/null; then
  echo
  echo "======================================================================"
  echo " ONE STEP IN SNOWFLAKE: in a Snowsight worksheet, paste these lines"
  echo " one at a time, as ACCOUNTADMIN:"
  echo "======================================================================"
  echo "USE ROLE ACCOUNTADMIN;"
  echo "CREATE USER IF NOT EXISTS DIALOG_PIPELINE TYPE = SERVICE DEFAULT_ROLE = CR_DATA_ENGINEER DEFAULT_WAREHOUSE = CR_LOAD_WH DEFAULT_NAMESPACE = CREDIT_RISK.RAW;"
  echo "ALTER USER DIALOG_PIPELINE SET RSA_PUBLIC_KEY = '${PUBKEY}';"
  echo "GRANT ROLE CR_DATA_ENGINEER TO USER DIALOG_PIPELINE;"
  echo "======================================================================"
  read -r -p "Press Enter when done in Snowflake... " _
  if ! run_test; then
    echo
    echo "Still cannot log in. Check: account identifier in $ENV_FILE, the key was pasted as one line,"
    echo "and snowflake/18_pipeline_setup_all.sql was run. Then run this script again."
    exit 1
  fi
fi

systemctl enable dialog-pipeline >/dev/null
systemctl restart dialog-pipeline
sleep 2
if systemctl is-active --quiet dialog-pipeline; then
  echo
  echo "======================================================================"
  echo " DONE. Every file saved by the upload page is now pushed to Snowflake"
  echo " and loaded into RAW within about a minute."
  echo " Watch it:   sudo journalctl -u dialog-pipeline -f"
  echo " Results:    the 'Snowflake' column on the upload page,"
  echo "             /data/dialog_uploads/pipeline_log.csv, and RAW.UPLOAD_LOG"
  echo "======================================================================"
else
  echo "The service did not start. See: sudo journalctl -u dialog-pipeline -n 50"
  exit 1
fi
