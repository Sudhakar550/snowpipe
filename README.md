# Dialog data upload portal (step 1: collect files)

A small web page where Dialog uploads the data files. Files of **any size** are accepted
(the browser sends them in 8 MB pieces), checked, and kept **unchanged** in a folder:

```
data/
  incoming/
    20261116_101500_dialog/          <- one folder per upload session (time + user)
      DF_CP_LOYALTY_FILTERED.csv
      DF_CP_REFILL_GSM_FILTERED.csv
  upload_log.csv                     <- one line per file: who, when, size, fingerprint, status
```

Step 2 (later) reads `upload_log.csv` / the `incoming` folders and pushes new files to
Snowflake (PUT to the stage, then `CALL CREDIT_RISK.RAW.LOAD_NEW_FILES()` from file 16).

## What each file goes through

| Check | If it fails |
|---|---|
| Name has only letters, digits, `_ . -` | Not uploaded, user is told to rename it |
| Name starts with one of the 23 known names (see `file_map.json`) | Not uploaded ("File name not recognised") |
| Extension is `.csv`, `.csv.gz` or `.csv_partN.gz` | Not uploaded |
| First line equals the expected header (checked in the browser **and** again on the server) | Not saved, status **REJECTED** |
| Same content uploaded before (SHA-256 fingerprint) | Not saved again, status **DUPLICATE** |
| All OK | Saved in `incoming/<session>/`, status **SAVED** |

Every result is in `upload_log.csv` and in the "Upload history" on the page.

## Run it

Needs Python 3.10 or newer.

**Windows (your laptop, to try it)**
```
cd upload_app
python -m pip install -r requirements.txt
set UPLOAD_USERS=dialog:ChooseAStrongPassword
python app.py
```
Open http://localhost:8000 and sign in with `dialog` / your password.
Or double-click `run_windows.bat` (edit the password inside first).

**Linux server (for example an EC2 instance, later)**
```
cd upload_app
python3 -m pip install -r requirements.txt
export UPLOAD_USERS="dialog:ChooseAStrongPassword,thbs:AnotherPassword"
export DATA_DIR=/data/dialog_uploads
./run_linux.sh
```

## Settings (environment variables)

| Name | Meaning | Default |
|---|---|---|
| `UPLOAD_USERS` | Logins, `user:password,user2:password2` | `dialog:change-me` (**change it**) |
| `DATA_DIR` | Where files and the log are kept | `./data` next to `app.py` |
| `PORT` | Web port | `8000` |
| `CHUNK_MB` | Piece size sent by the browser | `8` |

## Before Dialog uses it from outside

- Put it behind **HTTPS** (for example Nginx or a cloud load balancer with a certificate).
  The login is a simple browser sign-in, which is only safe over HTTPS.
- Change the default password.
- Make sure the disk has room: the files are kept as uploaded (all 23 files are about 2.5 GB,
  or about 0.4 GB as `.csv.gz`).
- If Nginx is in front, set `client_max_body_size 20m;` (pieces are 8 MB, so no bigger limit is needed).

## Files

| File | What it is |
|---|---|
| `app.py` | The server (FastAPI): login, piece upload, checks, folder, log |
| `static/index.html` | The page (no external libraries) |
| `file_map.json` | The 23 known files: name prefix, table, expected header (same as `RAW.FILE_MAP` in file 16) |
| `requirements.txt` | Python packages |
| `run_windows.bat`, `run_linux.sh` | Start scripts |
