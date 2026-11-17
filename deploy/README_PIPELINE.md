# Upload page to Snowflake: full setup on Ubuntu (EC2)

End result: Dialog uploads files on `https://<your-address>`, and within about a minute each file
is in the right `CREDIT_RISK.RAW` table, every row as is. The page shows the result.

```
Browser ──upload──> EC2: upload page ──saves──> /data/dialog_uploads/incoming/<batch>/
                                   └─ dialog-pipeline service (every 60 s)
                                         PUT ──> @CREDIT_RISK.RAW.STG_DIALOG/incoming/<batch>/
                                         CALL RAW.LOAD_NEW_FILES() ──> RAW tables (+ RAW.UPLOAD_LOG)
```

Time: about 45 minutes. Do the steps in order.

---

## Step 1. Snowflake (Snowsight worksheet, run one statement at a time)

Open `snowflake/18_pipeline_setup_all.sql` and choose **Run all** (arrow next to ▶). It creates
the file map (31 file types), upload log, Phase 3 tables, the loader (adds every row as is) and
the undo procedure, and gives the loader access to the CSV file format. Safe to run again.

Check: `SELECT COUNT(*) FROM CREDIT_RISK.RAW.FILE_MAP;` shows **31**.

## Step 2. EC2 instance

Follow `deploy/README_EC2.md` steps 1-3:
- Ubuntu 24.04, t3.small, 50 GB disk
- Security group: SSH from My IP, HTTP and HTTPS from anywhere
- Elastic IP attached

## Step 3. Upload page

From PowerShell on your laptop (replace the IP):

```powershell
scp -i "$env:USERPROFILE\Downloads\dialog-key.pem" "$env:USERPROFILE\Downloads\shared_data\upload_app.zip" ubuntu@<ELASTIC-IP>:~
ssh -i "$env:USERPROFILE\Downloads\dialog-key.pem" ubuntu@<ELASTIC-IP>
```

On the server:

```bash
sudo apt-get update && sudo apt-get install -y unzip
unzip -o upload_app.zip && cd upload_app
sudo bash deploy/setup_ec2.sh
```

It asks for the web address (press Enter), a login name and a password, then prints the link.
Open it and check that you can sign in.

## Step 4. Connect the server to Snowflake

Still on the server, in the `upload_app` folder:

```bash
sudo bash deploy/setup_pipeline.sh
```

1. It asks for the **account identifier**: press Enter for `COUTGXW-TK77861`.
2. It prints 4 SQL lines with the server's **public key**. Copy them into a Snowsight worksheet
   and run them one at a time (as ACCOUNTADMIN). This creates the user `DIALOG_PIPELINE`, which
   logs in with the key (no password, no MFA prompt).
3. Back on the server, press **Enter**. It tests the login, for example:
   `Connected to Snowflake as user=DIALOG_PIPELINE role=CR_DATA_ENGINEER ...`
   `RAW.FILE_MAP knows 31 file types`
4. It starts the service and prints **DONE**.

## Step 5. Test end to end

1. On the upload page, upload a small file, for example `DF_CP_MDA_VISITS_FILTERED.csv`.
2. History shows **Upload: Saved** and **Snowflake: Waiting**.
3. Within about a minute (the page refreshes every 30 seconds): **Snowflake: Loaded**, "4,121 rows added to RAW.MDA_VISITS".
5. In Snowsight:
   ```sql
   SELECT PROCESSED_AT, FILE_NAME, TARGET_TABLE, STATUS, ROWS_IN_FILE, ROWS_LOADED, ROWS_SKIPPED, MESSAGE
   FROM CREDIT_RISK.RAW.UPLOAD_LOG ORDER BY PROCESSED_AT DESC LIMIT 20;
   ```

## How duplicates are handled

Rows are loaded **as is**: nothing is removed. If a file repeats rows that are already in the
table (or repeats a row inside itself), those rows are added again. Each file in the stage is
loaded only once, and the upload page does not save the exact same file twice.
Use `_SOURCE_FILE` in the RAW tables to see which upload each row came from, and
`CALL CREDIT_RISK.RAW.REMOVE_UPLOAD('<path>')` to take an upload back out.

## Everyday commands (on the server)

| Task | Command |
|---|---|
| Watch the pipeline live | `sudo journalctl -u dialog-pipeline -f` |
| Is it running? | `sudo systemctl status dialog-pipeline` |
| Restart it | `sudo systemctl restart dialog-pipeline` |
| Push now, without waiting | `sudo systemctl restart dialog-pipeline` |
| Results file | `cat /data/dialog_uploads/pipeline_log.csv` |
| Test the Snowflake login | `sudo -u dialogapp bash -c 'export DATA_DIR=/data/dialog_uploads; set -a; . /etc/dialog-pipeline.env; set +a; /opt/dialog-upload/venv/bin/python /opt/dialog-upload/pipeline/pusher.py --test'` |
| Install a new version | copy the new zip, `unzip -o upload_app.zip && cd upload_app`, then `sudo bash deploy/setup_ec2.sh` and `sudo bash deploy/setup_pipeline.sh` (logins, keys and files are kept) |

## If something goes wrong

| Message | Fix |
|---|---|
| `JWT token is invalid` | The public key was not pasted correctly: run `ALTER USER DIALOG_PIPELINE SET RSA_PUBLIC_KEY = '...'` again with the key from `sudo cat /etc/dialog-pipeline/rsa_key.pub` (one line, without the BEGIN/END lines) |
| `Incorrect username` / `account not found` | Fix `SNOWFLAKE_ACCOUNT` in `/etc/dialog-pipeline.env`, then restart |
| `Insufficient privileges` on PUT or CALL | Run file 18 again (stage ownership, file format access) and check `GRANT ROLE CR_DATA_ENGINEER TO USER DIALOG_PIPELINE` |
| `RAW.FILE_MAP knows 23 file types` | Run file 18 |
| Snowflake status stays "Retrying" | `sudo journalctl -u dialog-pipeline -n 50` shows the reason; after 5 failed tries a file is marked "Failed" |

## Cost

- **EC2:** about $20–30 a month (see README_EC2.md).
- **Snowflake:** only when files arrive. Each round that finds new files starts CR_LOAD_WH (XSMALL) for about a minute. When nothing is uploaded, nothing runs in Snowflake.
