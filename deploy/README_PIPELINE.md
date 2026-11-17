# Step 2: connect the server to Snowflake

Result: every file saved by the upload page is pushed to Snowflake and loaded into its
`CREDIT_RISK.RAW` table, every row as is, within about a minute. The page shows the result.
Do `README_EC2.md` first. The full runbook (all commands) is in `../README.md`.

```
upload page ──saves──> /data/dialog_uploads/incoming/<batch>/
                         └─ dialog-pipeline service (every 60 s)
                              PUT  ──> @CREDIT_RISK.RAW.STG_DIALOG/incoming/<batch>/
                              CALL CREDIT_RISK.RAW.LOAD_NEW_FILES() ──> RAW tables + RAW.UPLOAD_LOG
                              result ──> pipeline_log.csv ──> page
```

Only RAW is loaded. CLEAN, FEATURES and SCORES are not touched.

---

## 1. Snowflake (Snowsight)

1. **+ Add new → SQL file**, paste `Downloads\shared_data\snowflake\18_pipeline_setup_all.sql`.
2. Arrow next to ▶ → **Run all**. Safe to run again.

It creates, in `CREDIT_RISK.RAW`:

| Object | Purpose |
|---|---|
| Stage directory + `FF_DIALOG_LINE` | List of stage files; reads a file's first line |
| Access to `FF_DIALOG_CSV` | The loader (role `CR_DATA_ENGINEER`) may use the CSV format |
| `FILE_MAP` | 31 file types: name prefix → table, expected header |
| `UPLOAD_LOG` | One row per file processed |
| 8 Phase 3 tables | Bills/payments, digital payments, GSM revenue, broadband usage |
| `LOAD_NEW_FILES()` | Loads new files from `incoming/`, rows as is (duplicate skipping built in, off) |
| `REMOVE_UPLOAD(path)` | Takes one upload back out |

Check:

```sql
SELECT COUNT(*) FROM CREDIT_RISK.RAW.FILE_MAP;   -- 31
CALL CREDIT_RISK.RAW.LOAD_NEW_FILES();           -- NOTHING_NEW
```

## 2. Server

```bash
cd ~/snowpipe
sudo bash deploy/setup_pipeline.sh
```

1. **Account identifier:** press Enter (`COUTGXW-TK77861`).
2. The first time, it prints 4 SQL lines with the server's public key. Run them in Snowsight
   one at a time. They create the user `DIALOG_PIPELINE`, which logs in with the key (no
   password, no MFA). Then press **Enter** on the server.
3. It tests the login:
   ```
   Connected to Snowflake as user=DIALOG_PIPELINE role=CR_DATA_ENGINEER warehouse=CR_LOAD_WH database=CREDIT_RISK
   RAW.FILE_MAP knows 31 file types
   ```
4. It starts the service `dialog-pipeline` and prints **DONE**.

Running it again is safe: the key and settings are kept.

## 3. Test

1. Upload a small file on the page, e.g. `DF_CP_MDA_VISITS_FILTERED.csv`.
2. Watch: `sudo journalctl -u dialog-pipeline -f` (Ctrl+C stops watching, not the service).
3. Expect `pushed …` then `LOADED … rows added to RAW.MDA_VISITS`; the page shows **Loaded**.

## Statuses on the page

| Snowflake column | Meaning |
|---|---|
| Waiting | Saved on the server, not pushed yet |
| Retrying | A try failed; tried again next round (up to 5 times) |
| Loaded | Rows added to the RAW table; row count shown |
| Failed | Header wrong, or a Snowflake error. Reason shown under the file |

## Retry failed files

Failed files loaded no rows, so retrying does not double anything.

```bash
sudo systemctl stop dialog-pipeline                                        # server
```
```sql
DELETE FROM CREDIT_RISK.RAW.UPLOAD_LOG WHERE STATUS = 'FAILED';            -- Snowsight
```
```bash
sudo sed -i -E '/,(FAILED|GAVE_UP),/d' /data/dialog_uploads/pipeline_log.csv   # server
sudo systemctl start dialog-pipeline
```

## Duplicates

- Rows are loaded **as is** (current setting).
- Exact same file again: blocked by the page (**Already uploaded**).
- Same name, new contents: loaded, rows **added on top**.
- Corrected full copy: first `CALL CREDIT_RISK.RAW.REMOVE_UPLOAD('<RELATIVE_PATH from UPLOAD_LOG>');`, then upload.
- Duplicate skipping is built in but off (`RAW.FILE_MAP.DUPLICATE_RULE = 'KEEP_ALL'`).
  To turn it on: `UPDATE CREDIT_RISK.RAW.FILE_MAP SET DUPLICATE_RULE = 'NEW_ROWS_ONLY';` (see `../README.md`, section 9).

## Commands

| Task | Command |
|---|---|
| Live log | `sudo journalctl -u dialog-pipeline -f` |
| Last 50 lines | `sudo journalctl -u dialog-pipeline -n 50 --no-pager -l` |
| Status | `sudo systemctl status dialog-pipeline --no-pager` |
| Push now | `sudo systemctl restart dialog-pipeline` |
| Pause / resume | `sudo systemctl stop dialog-pipeline` / `sudo systemctl start dialog-pipeline` |
| Results file | `cat /data/dialog_uploads/pipeline_log.csv` |
| Test login | `sudo -u dialogapp bash -c 'export DATA_DIR=/data/dialog_uploads; set -a; . /etc/dialog-pipeline.env; set +a; /opt/dialog-upload/venv/bin/python /opt/dialog-upload/pipeline/pusher.py --test'` |
| New code version | `cd ~/snowpipe && git pull && sudo bash deploy/update.sh` |

## If something goes wrong

| Message | Fix |
|---|---|
| `JWT token is invalid` | `sudo cat /etc/dialog-pipeline/rsa_key.pub`; in Snowsight `ALTER USER DIALOG_PIPELINE SET RSA_PUBLIC_KEY = '<key on one line, without BEGIN/END>';` |
| `Incorrect username` / account not found | Fix `SNOWFLAKE_ACCOUNT` in `/etc/dialog-pipeline.env`, then `sudo systemctl restart dialog-pipeline` |
| `FILE_MAP does not exist` / `knows 23 file types` | Run file 18 (**Run all**) |
| `File format … not authorized` / `Insufficient privileges` | Run file 18 again (stage ownership and file format access), then retry failed files |
| Stays "Retrying" | `sudo journalctl -u dialog-pipeline -n 50` shows the reason |

## Cost

Snowflake runs only when files arrive: each round with new files starts `CR_LOAD_WH` (XSMALL)
for about a minute. When nothing is uploaded, nothing runs in Snowflake.

## Secrets

`/etc/dialog-pipeline/rsa_key.p8` is the private key. Never share it, paste it in chat or
commit it. If it leaks: delete it, run `setup_pipeline.sh` again (it makes a new key) and set the
new public key on `DIALOG_PIPELINE`.
