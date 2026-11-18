# Dialog data upload → Snowflake RAW

Dialog uploads data files on a web page. Each file is checked, saved on an AWS server, and
loaded into the matching `CREDIT_RISK.RAW` table in Snowflake within a few minutes.
Rows already in the table are skipped (duplicate check, see section 9).
Any file size works.

| | |
|---|---|
| Upload page | `https://3.26.129.230` (browser warning once: **Advanced → Continue**) |
| Server | AWS EC2, Ubuntu 24.04, Elastic IP `3.26.129.230`, user `ubuntu`, key `dialog-key.pem` |
| Code | GitHub `Sudhakar550/snowpipe` (this folder). Laptop copy: `Downloads\shared_data\upload_app` |
| Snowflake | Account `COUTGXW-TK77861`, database `CREDIT_RISK`, schema `RAW` |
| Snowflake setup | `Downloads\shared_data\snowflake\18_pipeline_setup_all.sql` (one file) |

Only **RAW** is loaded. CLEAN, FEATURES and SCORES are not touched.

---

## Contents

1. [How it works](#1-how-it-works)
2. [What is in this folder](#2-what-is-in-this-folder)
3. [Full setup from zero](#3-full-setup-from-zero)
4. [Daily use](#4-daily-use)
5. [Change the code (laptop → GitHub → server)](#5-change-the-code)
6. [Logins for the upload page](#6-logins-for-the-upload-page)
7. [Start, stop, status, logs](#7-start-stop-status-logs)
8. [Snowflake checks and fixes](#8-snowflake-checks-and-fixes)
9. [Duplicates and re-sent files](#9-duplicates-and-re-sent-files)
10. [Web address and HTTPS (Caddy)](#10-web-address-and-https-caddy)
11. [Troubleshooting](#11-troubleshooting)
12. [Stop everything / cost](#12-stop-everything--cost)

---

## 1. How it works

```
Browser ──HTTPS──> Caddy ──> upload page (app.py) ──> /data/dialog_uploads/incoming/<batch>/<file>
                                    │                    + upload_log.csv
                                    │
                    dialog-pipeline service (pusher.py, every 60 s)
                       PUT  ──> Snowflake stage @CREDIT_RISK.RAW.STG_DIALOG/incoming/<batch>/
                       CALL CREDIT_RISK.RAW.LOAD_NEW_FILES()
                              ├─ FILE_MAP: which file goes to which table, expected columns
                              ├─ checks the header, adds rows not already in the table (+ _SOURCE_FILE)
                              └─ writes the result to RAW.UPLOAD_LOG
                       writes the result to pipeline_log.csv ──> shown on the page
```

| Step | Where | What happens |
|---|---|---|
| 1. Add file | Browser | Name must match one of the 31 file types; first line must be the expected header |
| 2. Upload | Browser → `app.py` | Sent in 8 MB pieces, 3 at a time; a dropped connection resends one piece |
| 3. Save | `app.py` | Checks size, header and fingerprint (SHA-256). Saves to `incoming/<batch>/`, writes `upload_log.csv` |
| 4. Push | `pusher.py` | Every 60 s: compresses and `PUT`s new files to the Snowflake stage |
| 5. Load | `LOAD_NEW_FILES()` | Header check, adds rows not already in the RAW table, result in `RAW.UPLOAD_LOG` |
| 6. Show | `pusher.py` → page | Result in `pipeline_log.csv`; the page refreshes every 20 s |

The page reads only from the server (`file_map.json`, `upload_log.csv`, `pipeline_log.csv`),
not from Snowflake directly.

| Part | Why |
|---|---|
| EC2 + Elastic IP | Anyone with the link can upload, runs 24/7, fixed address |
| Caddy | HTTPS in front of the app; the app itself is only reachable inside the server |
| FastAPI (`app.py`) | Chunked upload, so no size limit (Snowsight stops at 250 MB, Streamlit at 200 MB) |
| systemd services | Page and pipeline start on boot and restart if they stop |
| Key-pair login (`DIALOG_PIPELINE`) | The server logs in to Snowflake without a password or MFA |

---

## 2. What is in this folder

| File | Purpose |
|---|---|
| `app.py` | Upload page server: login, chunked upload, checks, save, history |
| `static/index.html` | The upload page |
| `file_map.json` | The 31 file types: name prefix, RAW table, expected header, phase |
| `pipeline/pusher.py` | Pushes saved files to Snowflake and records results |
| `deploy/setup_ec2.sh` | Installs the page + Caddy on a new server |
| `deploy/setup_pipeline.sh` | Creates the Snowflake key, connects, starts the pipeline |
| `deploy/update.sh` | Installs a new version of the code (keeps logins, key, files) |
| `deploy/README_EC2.md`, `deploy/README_PIPELINE.md` | Details for the two setup steps |
| `requirements.txt`, `run_windows.bat`, `run_linux.sh` | Python packages; run locally to try the page |

On the server (not in Git, they hold secrets):

| Path | Contains |
|---|---|
| `/opt/dialog-upload/` | Installed app, Python environment, `pipeline/pusher.py` |
| `/data/dialog_uploads/incoming/` | Uploaded files, one folder per batch |
| `/data/dialog_uploads/upload_log.csv` | Every upload: who, when, size, status |
| `/data/dialog_uploads/pipeline_log.csv` | Snowflake result per file |
| `/etc/dialog-upload.env` | Upload logins (`UPLOAD_USERS`) |
| `/etc/dialog-pipeline.env` | Snowflake account, user, role, warehouse |
| `/etc/dialog-pipeline/rsa_key.p8` | Private key for Snowflake. **Never share or commit it.** |
| `/etc/caddy/Caddyfile` | Web address and HTTPS settings |

---

## 3. Full setup from zero

About 45 minutes. Do the steps in order.

### 3.1 Snowflake (Snowsight)

1. **+ Add new → SQL file**, paste `18_pipeline_setup_all.sql`.
2. Arrow next to ▶ → **Run all**. Safe to run again.
3. Check:
   ```sql
   SELECT COUNT(*) FROM CREDIT_RISK.RAW.FILE_MAP;   -- 31
   CALL CREDIT_RISK.RAW.LOAD_NEW_FILES();           -- NOTHING_NEW
   ```

### 3.2 AWS: server

EC2 → **Launch instance**: Ubuntu Server 24.04, **t3.small**, 50 GB gp3, key pair `dialog-key` (.pem).
Security group: **SSH** from My IP, **HTTP** and **HTTPS** from Anywhere.
Then **Elastic IPs → Allocate → Associate** with the instance. Details: `deploy/README_EC2.md`.

### 3.3 Connect (PowerShell on the laptop)

```powershell
icacls "$env:USERPROFILE\Downloads\dialog-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i "$env:USERPROFILE\Downloads\dialog-key.pem" ubuntu@3.26.129.230
```

### 3.4 Get the code (server)

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Sudhakar550/snowpipe.git ~/snowpipe     # private repo: use a GitHub token as the password
cd ~/snowpipe
```

### 3.5 Install the upload page (server)

```bash
sudo bash deploy/setup_ec2.sh
```

1. **Web address:** press Enter to use the server IP (works behind office firewalls).
   Type a domain only if IT has pointed one at the Elastic IP.
2. **Login name** (Enter = `dialog`) and **password** (10+ characters, no `:` or `,`).
3. It ends with `DONE. Upload page: https://3.26.129.230`.

### 3.6 Connect the server to Snowflake (server)

```bash
sudo bash deploy/setup_pipeline.sh
```

1. Account identifier: press Enter (`COUTGXW-TK77861`).
2. If it prints 4 SQL lines with a public key: run them in Snowsight one at a time, then press Enter.
3. It must show `RAW.FILE_MAP knows 31 file types` and end with **DONE**.

### 3.7 Test end to end

1. Open `https://3.26.129.230` → **Advanced → Continue** → sign in.
2. Upload a small file, e.g. `DF_CP_MDA_VISITS_FILTERED.csv`.
3. On the server, watch it: `sudo journalctl -u dialog-pipeline -f` (Ctrl+C to stop watching).
4. Within about a minute the page shows **Snowflake: Loaded** with the row count.

---

## 4. Daily use

1. Open the page, sign in, drag in the files, press **Upload**. Keep the tab open until each file shows **Saved**.
2. Wait for **Loaded** in the history (a few minutes for big files).
3. Problems show under the file with a short reason; **Technical details** has the full message.

Accepted names: the **Accepted files** panel on the page, or `file_map.json`.

---

## 5. Change the code

**Laptop** (Command Prompt):

```
cd C:\Users\sudhakar_shivraj\Downloads\shared_data\upload_app
git status
git add -A
git commit -m "Describe the change"
git push
```

**Server:**

```bash
cd ~/snowpipe && git pull
sudo bash deploy/update.sh
```

Then reload the page with **Ctrl+F5**.

If a change touches `setup_ec2.sh` or `setup_pipeline.sh` themselves, run that script again
instead (`sudo bash deploy/setup_ec2.sh` / `setup_pipeline.sh`). Both keep logins, key and files.

---

## 6. Logins for the upload page

All on the server. Format: `UPLOAD_USERS=user:password,user2:password2`.
Passwords: 10+ characters, no `:` `,` (and no `|` when using the sed commands).

| Task | Command |
|---|---|
| View logins | `sudo grep UPLOAD_USERS /etc/dialog-upload.env` |
| Edit by hand | `sudo nano /etc/dialog-upload.env` (save Ctrl+O, Enter; exit Ctrl+X) |
| Replace all logins | `sudo sed -i 's\|^UPLOAD_USERS=.*\|UPLOAD_USERS=dialog:NewPass2026,sudhakar:Other2026\|' /etc/dialog-upload.env` |
| Add one user | `sudo sed -i 's\|^UPLOAD_USERS=.*\|&,newuser:NewUserPass2026\|' /etc/dialog-upload.env` |
| Remove a user | `sudo sed -i 's\|,newuser:[^,]*\|\|; s\|=newuser:[^,]*,\|=\|' /etc/dialog-upload.env` |
| Apply any change | `sudo systemctl restart dialog-upload` |

In the table, `\|` is how Markdown shows `|`; type a plain `|`. For example:

```bash
sudo sed -i 's|^UPLOAD_USERS=.*|UPLOAD_USERS=dialog:NewPass2026|' /etc/dialog-upload.env
sudo systemctl restart dialog-upload
sudo grep UPLOAD_USERS /etc/dialog-upload.env
```

If the browser keeps the old login, open the page in a private window.

---

## 7. Start, stop, status, logs

| Task | Command |
|---|---|
| Status of all three | `sudo systemctl is-active dialog-upload dialog-pipeline caddy` |
| Full status | `sudo systemctl status dialog-upload dialog-pipeline caddy --no-pager` |
| Restart all | `sudo systemctl restart dialog-upload dialog-pipeline caddy` |
| Pause loading to Snowflake | `sudo systemctl stop dialog-pipeline` |
| Resume loading | `sudo systemctl start dialog-pipeline` |
| Push now (don't wait 60 s) | `sudo systemctl restart dialog-pipeline` |
| Pipeline live | `sudo journalctl -u dialog-pipeline -f` |
| Pipeline last 50 lines | `sudo journalctl -u dialog-pipeline -n 50 --no-pager -l` |
| Upload page messages | `sudo journalctl -u dialog-upload -n 50 --no-pager -l` |
| Caddy messages | `sudo journalctl -u caddy -n 20 --no-pager -l` |
| Upload history file | `cat /data/dialog_uploads/upload_log.csv` |
| Snowflake results file | `cat /data/dialog_uploads/pipeline_log.csv` |
| Uploaded files | `ls -lh /data/dialog_uploads/incoming/*/` |
| Disk space | `df -h /` |

Test the Snowflake login, or run one round by hand:

```bash
sudo -u dialogapp bash -c 'export DATA_DIR=/data/dialog_uploads; set -a; . /etc/dialog-pipeline.env; set +a; /opt/dialog-upload/venv/bin/python /opt/dialog-upload/pipeline/pusher.py --test'
sudo -u dialogapp bash -c 'export DATA_DIR=/data/dialog_uploads; set -a; . /etc/dialog-pipeline.env; set +a; /opt/dialog-upload/venv/bin/python /opt/dialog-upload/pipeline/pusher.py --once'
```

---

## 8. Snowflake checks and fixes

```sql
USE ROLE CR_DATA_ENGINEER;
USE WAREHOUSE CR_LOAD_WH;

-- result per file
SELECT PROCESSED_AT, FILE_NAME, TARGET_TABLE, STATUS, ROWS_IN_FILE, ROWS_LOADED, MESSAGE
FROM CREDIT_RISK.RAW.UPLOAD_LOG ORDER BY PROCESSED_AT DESC LIMIT 20;

-- files in the stage
LIST @CREDIT_RISK.RAW.STG_DIALOG/incoming/;

-- rows per upload in one table
SELECT _SOURCE_FILE, COUNT(*) FROM CREDIT_RISK.RAW.BILLS_PAYMENTS_GSM GROUP BY 1;

-- take one upload back out (path = RELATIVE_PATH from UPLOAD_LOG)
CALL CREDIT_RISK.RAW.REMOVE_UPLOAD('incoming/<batch>/<file>.csv.gz');

-- load new stage files by hand (normally the pipeline does this)
CALL CREDIT_RISK.RAW.LOAD_NEW_FILES();
```

**Retry files that failed** (they loaded no rows, so retrying does not double anything):

```bash
sudo systemctl stop dialog-pipeline                                   # server
```
```sql
DELETE FROM CREDIT_RISK.RAW.UPLOAD_LOG WHERE STATUS = 'FAILED';       -- Snowsight
```
```bash
sudo sed -i -E '/,(FAILED|GAVE_UP),/d' /data/dialog_uploads/pipeline_log.csv   # server
sudo systemctl start dialog-pipeline
sudo journalctl -u dialog-pipeline -f
```

---

## 9. Duplicates and re-sent files

Two checks stop the same data from being loaded twice.

**1. Same file (upload page).** A file with exactly the same contents as an earlier upload,
even under another name, is not saved: **Already uploaded**.

**2. Same rows (Snowflake loader, ON).** Each row of a new file is compared with the rows
already in its RAW table (all columns except `_SOURCE_FILE` / `_LOADED_AT`). Rows already there
are not inserted. The comparison is copy by copy, because identical rows can be real (for
example 13 payments of Rs 500 on one day): if the table holds 13 copies and the file 14, one is added.

| Upload | Result |
|---|---|
| A file sent again (same rows) | Nothing added: **No new rows** |
| Old months plus a new month | Only the new rows added; page shows "N duplicates skipped" |
| 13 identical payments in a new file | All 13 kept the first time; none added if sent again |
| A corrected row (one value changed) | Added as a new row; the old one stays. Use `REMOVE_UPLOAD` on the old upload to replace it |

### The setting

At the top of `18_pipeline_setup_all.sql`:

```sql
SET DUPLICATE_RULE_FOR_ALL = 'NEW_ROWS_ONLY';
```

| Value | Behaviour |
|---|---|
| `NEW_ROWS_ONLY` (current) | Skip rows already in the table, keep real repeats |
| `ONE_COPY` | Same, and a row repeated inside a file is kept only once |
| `KEEP_ALL` | No check: every row loaded as is |

Run all applies it to all 31 tables. To change it without running the file:

```sql
UPDATE CREDIT_RISK.RAW.FILE_MAP SET DUPLICATE_RULE = 'KEEP_ALL';                                     -- all tables
UPDATE CREDIT_RISK.RAW.FILE_MAP SET DUPLICATE_RULE = 'ONE_COPY' WHERE TARGET_TABLE = 'GSM_APP_USAGE';   -- one table

SELECT TARGET_TABLE, DUPLICATE_RULE FROM CREDIT_RISK.RAW.FILE_MAP ORDER BY 1;                         -- see it
SELECT CONTAINS(GET_DDL('PROCEDURE', 'CREDIT_RISK.RAW.LOAD_NEW_FILES()'), 'load_new_rows') AS NEW_LOADER;   -- TRUE = current loader
```

A change applies from the next upload. Rows loaded earlier are not changed. Each upload stays
separate (`_SOURCE_FILE`), so any single upload can be removed:

```sql
SELECT RELATIVE_PATH, FILE_NAME, ROWS_IN_FILE, ROWS_LOADED, ROWS_SKIPPED, PROCESSED_AT
FROM CREDIT_RISK.RAW.UPLOAD_LOG ORDER BY PROCESSED_AT DESC LIMIT 20;

CALL CREDIT_RISK.RAW.REMOVE_UPLOAD('<RELATIVE_PATH>');
```

---

## 10. Web address and HTTPS (Caddy)

Caddy receives the browser connection (HTTPS, port 443) and passes it to the app on
`127.0.0.1:8000`.

| Setup | Address | Certificate |
|---|---|---|
| Now | `https://3.26.129.230` | Self-made (`tls internal`): encrypted, browser shows "Not secure" once |
| With a company domain | e.g. `https://dialog-upload.thbs.com` | Free trusted certificate, no warning |

Why not sslip.io: the office firewall (FortiGuard, category "Dynamic DNS") blocks it.

**Switch to a domain** (after IT points it at `3.26.129.230`):

```bash
cd ~/snowpipe
sudo bash deploy/setup_ec2.sh        # type the domain at "Web address"
```

Commands:

```bash
cat /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy
sudo journalctl -u caddy -n 20 --no-pager -l
```

---

## 11. Troubleshooting

| What you see | Fix |
|---|---|
| Page blocked by FortiGuard | Use `https://3.26.129.230`, not the sslip.io address |
| Page does not open at all | Security group must allow 80 and 443 from 0.0.0.0/0; `sudo systemctl is-active caddy dialog-upload` |
| Login box keeps coming back | Wrong password: `sudo grep UPLOAD_USERS /etc/dialog-upload.env` |
| "File name not recognised" | Name must start with one of the 31 names (Accepted files panel) |
| "Columns don't match" | The file's header differs from `file_map.json`; ask Dialog if the change is intended |
| Snowflake: Waiting for long | `sudo systemctl is-active dialog-pipeline`; `sudo journalctl -u dialog-pipeline -n 50` |
| "Snowflake permission problem" | Run `18_pipeline_setup_all.sql` again (**Run all**), then [retry failed files](#8-snowflake-checks-and-fixes) |
| `JWT token is invalid` | Key not set right: `sudo cat /etc/dialog-pipeline/rsa_key.pub`, then in Snowsight `ALTER USER DIALOG_PIPELINE SET RSA_PUBLIC_KEY='<key on one line, no BEGIN/END>';` |
| `RAW.FILE_MAP knows 23 file types` / FILE_MAP does not exist | Run `18_pipeline_setup_all.sql` (**Run all**) |
| Account not found / `Incorrect username` | Fix `SNOWFLAKE_ACCOUNT` in `/etc/dialog-pipeline.env`, then `sudo systemctl restart dialog-pipeline` |
| Disk full | `df -h /`; old batches in `/data/dialog_uploads/incoming/` can be archived once loaded |

---

## 12. Stop everything / cost

| Task | How |
|---|---|
| Pause loading only | `sudo systemctl stop dialog-pipeline` |
| Take the page offline | `sudo systemctl stop dialog-upload` |
| Stop the server (saves money) | EC2 → instance → **Instance state → Stop**. Files are kept; start it again and the same address works. |

| Item | Approx. per month |
|---|---|
| EC2 t3.small running 24/7 | $15–20 |
| 50 GB disk | $4–5 |
| Elastic IP | about $3.6 |
| Snowflake | Only when files arrive: CR_LOAD_WH (XSMALL) runs about a minute per round with new files |

**Snowflake trial:** when it ends, the account is suspended until it is upgraded. Uploads still
save on the server; nothing loads until then. Failed rounds are retried (5 times per file), then
use [retry failed files](#8-snowflake-checks-and-fixes).

---

## Try the page on your laptop (optional)

```
cd C:\Users\sudhakar_shivraj\Downloads\shared_data\upload_app
python -m pip install -r requirements.txt
set UPLOAD_USERS=dialog:TestPassword123
python app.py
```

Open http://localhost:8000. Files go to `upload_app\data\`. Nothing is sent to Snowflake.
