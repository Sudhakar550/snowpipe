"""
Dialog upload pipeline - step 2: push saved uploads to Snowflake and load them into RAW.

Runs on the EC2 server as the service "dialog-pipeline" (see deploy/setup_pipeline.sh).
Every PIPELINE_INTERVAL seconds it:
  1. reads <DATA_DIR>/upload_log.csv (written by the upload page) and finds files with
     status SAVED that are not finished in <DATA_DIR>/pipeline_log.csv
  2. PUTs each one to @CREDIT_RISK.RAW.STG_DIALOG/incoming/<batch>/ (same folder name as on
     the server, compressed on the way, any size)
  3. CALLs CREDIT_RISK.RAW.LOAD_NEW_FILES()  (file 18: header check, rows added as is)
  4. writes the result per file to <DATA_DIR>/pipeline_log.csv, which the upload page shows

Usage:
  python pusher.py            run for ever (the service)
  python pusher.py --once     one round, then stop
  python pusher.py --test     only test the Snowflake login and print who we are

Settings (environment variables, from /etc/dialog-pipeline.env):
  SNOWFLAKE_ACCOUNT           e.g. COUTGXW-TK77861
  SNOWFLAKE_USER              DIALOG_PIPELINE
  SNOWFLAKE_PRIVATE_KEY_FILE  /etc/dialog-pipeline/rsa_key.p8
  SNOWFLAKE_ROLE              CR_DATA_ENGINEER
  SNOWFLAKE_WAREHOUSE         CR_LOAD_WH
  DATA_DIR                    /data/dialog_uploads
  PIPELINE_INTERVAL           seconds between rounds (default 60)
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data/dialog_uploads"))
UPLOAD_LOG = DATA_DIR / "upload_log.csv"
PIPELINE_LOG = DATA_DIR / "pipeline_log.csv"
STAGE = "@CREDIT_RISK.RAW.STG_DIALOG"
INTERVAL = int(os.environ.get("PIPELINE_INTERVAL", "60"))
MAX_TRIES = 5
FINAL = {"LOADED", "NO_NEW_ROWS", "SKIPPED_DUPLICATE", "FAILED", "GAVE_UP"}
COLUMNS = ["pushed_at", "saved_path", "stage_path", "target_table", "status",
           "rows_in_file", "rows_added", "rows_skipped", "message", "tries"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dialog-pipeline")


# ----------------------------------------------------------------- local logs
def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_pipeline_log(row: dict) -> None:
    new = not PIPELINE_LOG.exists()
    with open(PIPELINE_LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in COLUMNS})


def pending_files() -> list[dict]:
    """SAVED uploads that have no final result yet (and have not failed MAX_TRIES times)."""
    last: dict[str, dict] = {}
    for r in read_csv(PIPELINE_LOG):
        last[r["saved_path"]] = r
    todo = []
    for r in read_csv(UPLOAD_LOG):
        p = r.get("saved_path") or ""
        if r.get("status") != "SAVED" or not p:
            continue
        prev = last.get(p)
        if prev and prev["status"] in FINAL:
            continue
        r["tries"] = int(prev["tries"]) if prev and prev.get("tries", "").isdigit() else 0
        if r["tries"] >= MAX_TRIES:
            continue
        todo.append(r)
    return todo


# ----------------------------------------------------------------- Snowflake
def connect():
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    key_path = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]
    with open(key_path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    der = key.private_bytes(encoding=serialization.Encoding.DER,
                            format=serialization.PrivateFormat.PKCS8,
                            encryption_algorithm=serialization.NoEncryption())
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ.get("SNOWFLAKE_USER", "DIALOG_PIPELINE"),
        private_key=der,
        role=os.environ.get("SNOWFLAKE_ROLE", "CR_DATA_ENGINEER"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "CR_LOAD_WH"),
        database="CREDIT_RISK",
        schema="RAW",
        application="dialog_upload_pipeline",
        login_timeout=60,
    )


def rows_as_dicts(cur) -> list[dict]:
    names = [d[0].upper() for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def put_file(cur, local: Path, stage_dir: str) -> str:
    """Upload one file; returns its path inside the stage (e.g. incoming/<batch>/<name>.gz)."""
    target_dir = stage_dir.rstrip("/") + "/"
    cur.execute(f"PUT 'file://{local.as_posix()}' {STAGE}/{target_dir} "
                f"AUTO_COMPRESS=TRUE OVERWRITE=TRUE PARALLEL=4")
    res = rows_as_dicts(cur)
    status = str(res[0].get("STATUS", "")).upper() if res else ""
    if status not in ("UPLOADED", "SKIPPED"):
        raise RuntimeError(f"PUT returned {res}")
    return target_dir + str(res[0]["TARGET"])


def latest_snowflake_result(cur, stage_path: str) -> dict | None:
    cur.execute("SELECT TARGET_TABLE, STATUS, ROWS_LOADED, ROWS_IN_FILE, ROWS_SKIPPED, MESSAGE "
                "FROM CREDIT_RISK.RAW.UPLOAD_LOG WHERE RELATIVE_PATH = %s "
                "ORDER BY PROCESSED_AT DESC LIMIT 1", (stage_path,))
    res = rows_as_dicts(cur)
    return res[0] if res else None


def one_round() -> int:
    todo = pending_files()
    if not todo:
        return 0
    log.info("%d file(s) to push", len(todo))
    conn = connect()
    done = 0
    try:
        cur = conn.cursor()
        pushed: list[tuple[dict, str]] = []
        for f in todo:
            local = DATA_DIR / f["saved_path"]
            stage_dir = str(Path(f["saved_path"]).parent.as_posix())       # incoming/<batch>
            try:
                if not local.exists():
                    raise FileNotFoundError(f"{local} is missing on the server")
                stage_path = put_file(cur, local, stage_dir)
                log.info("pushed %s -> %s", f["saved_path"], stage_path)
                pushed.append((f, stage_path))
            except Exception as e:  # keep going with the other files, retry this one next round
                tries = f["tries"] + 1
                append_pipeline_log(dict(pushed_at=now(), saved_path=f["saved_path"], target_table=f.get("target_table"),
                                         status="GAVE_UP" if tries >= MAX_TRIES else "RETRY",
                                         message=f"Upload to Snowflake failed: {e}", tries=tries))
                log.warning("push failed for %s: %s", f["saved_path"], e)

        if not pushed:
            return 0
        cur.execute("CALL CREDIT_RISK.RAW.LOAD_NEW_FILES()")
        results = {r["RELATIVE_PATH"]: r for r in rows_as_dicts(cur)}
        for f, stage_path in pushed:
            r = results.get(stage_path) or latest_snowflake_result(cur, stage_path)
            if r is None:
                append_pipeline_log(dict(pushed_at=now(), saved_path=f["saved_path"], stage_path=stage_path,
                                         target_table=f.get("target_table"), status="RETRY", tries=f["tries"] + 1,
                                         message="Pushed, but the loader did not report this file yet."))
                continue
            append_pipeline_log(dict(
                pushed_at=now(), saved_path=f["saved_path"], stage_path=stage_path,
                target_table=r.get("TARGET_TABLE") or f.get("target_table"), status=r.get("STATUS"),
                rows_in_file=r.get("ROWS_IN_FILE") or "", rows_added=r.get("ROWS_LOADED") or 0,
                rows_skipped=r.get("ROWS_SKIPPED") or "", message=r.get("MESSAGE") or "", tries=f["tries"] + 1))
            log.info("%s: %s %s", stage_path, r.get("STATUS"), r.get("MESSAGE"))
            done += 1
    finally:
        conn.close()
    return done


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    if "--test" in sys.argv:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE()")
        print("Connected to Snowflake as user=%s role=%s warehouse=%s database=%s" % cur.fetchone())
        cur.execute("SELECT COUNT(*) FROM CREDIT_RISK.RAW.FILE_MAP")
        n = cur.fetchone()[0]
        print(f"RAW.FILE_MAP knows {n} file types" + ("" if n >= 31 else "  <-- expected 31: run snowflake/18_pipeline_setup_all.sql"))
        conn.close()
        return
    if "--once" in sys.argv:
        one_round()
        return
    log.info("pipeline started: watching %s every %ss", UPLOAD_LOG, INTERVAL)
    while True:
        try:
            one_round()
        except Exception as e:  # e.g. Snowflake not reachable: wait and try again
            log.error("round failed: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
