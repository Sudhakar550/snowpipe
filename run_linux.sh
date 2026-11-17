#!/usr/bin/env bash
# Start the upload portal. Set UPLOAD_USERS (and optionally DATA_DIR, PORT) before running.
cd "$(dirname "$0")"
exec python3 app.py
