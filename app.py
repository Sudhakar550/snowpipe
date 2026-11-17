"""
Dialog data upload portal - step 1: collect files in a folder.

Run:   python app.py        (then open http://localhost:8000)
Needs: pip install -r requirements.txt

What it does
  * A web page where Dialog uploads the data files (any size: the browser sends
    each file in 8 MB pieces, so there is no upload limit and a dropped
    connection only re-sends one piece).
  * Every file is checked: its name must match one of the 23 known files and
    its first line must be the expected header.
  * Accepted files are saved, unchanged, in
        <DATA_DIR>/incoming/<batch>/<file name>
    one batch folder per upload session, e.g. incoming/20261116_101500_dialog/
  * The exact same file (same content) uploaded again is recorded but not saved
    a second time.
  * Every file is written to <DATA_DIR>/upload_log.csv (who, when, size,
    SHA-256 fingerprint, status, message). This log is what the later
    Snowflake step reads to know which files are new.

Settings (environment variables, all optional)
  DATA_DIR        where files are kept            default: ./data
  UPLOAD_USERS    logins, "user:password,user2:password2"
                  default: dialog:change-me   (CHANGE THIS before sharing)
  PORT            web port                        default: 8000
  HOST            address to listen on            default: 0.0.0.0 (use 127.0.0.1 behind Caddy/Nginx)
  CHUNK_MB        piece size sent by the browser  default: 8
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# --------------------------------------------------------------------------- settings
BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data")).resolve()
INCOMING = DATA_DIR / "incoming"
TMP = DATA_DIR / "_tmp"
LOG_FILE = DATA_DIR / "upload_log.csv"
CHUNK = int(os.environ.get("CHUNK_MB", "8")) * 1024 * 1024
USERS = dict(u.split(":", 1) for u in os.environ.get("UPLOAD_USERS", "dialog:change-me").split(",") if ":" in u)
FILE_MAP = json.loads((BASE / "file_map.json").read_text(encoding="utf-8"))

LOG_COLUMNS = ["uploaded_at", "user", "batch", "file_name", "target_table", "size_bytes",
               "sha256", "status", "message", "saved_path"]
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
ALLOWED_EXT = re.compile(r"(\.csv|\.csv\.gz|\.csv_part\d+\.gz)$", re.IGNORECASE)

for d in (INCOMING, TMP):
    d.mkdir(parents=True, exist_ok=True)
_log_lock = threading.Lock()
_uploads: dict[str, dict] = {}          # upload_id -> state (in memory while uploading)
_uploads_lock = threading.Lock()

app = FastAPI(title="Dialog data upload")
security = HTTPBasic()


# --------------------------------------------------------------------------- helpers
def current_user(cred: HTTPBasicCredentials = Depends(security)) -> str:
    """Simple login: the browser asks for user + password (HTTP Basic)."""
    pw = USERS.get(cred.username)
    if pw is None or not secrets.compare_digest(pw.encode(), cred.password.encode()):
        raise HTTPException(401, "Wrong user or password", headers={"WWW-Authenticate": "Basic"})
    return cred.username


def match_file(name: str) -> dict | None:
    """Longest known prefix the file name starts with (case-insensitive)."""
    best = None
    for m in FILE_MAP:
        if name.upper().startswith(m["file_prefix"].upper()):
            if best is None or len(m["file_prefix"]) > len(best["file_prefix"]):
                best = m
    return best


def header_of(path: Path) -> list[str]:
    """First line of a .csv or gzip file, as a list of upper-case column names."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rb") as fh:
        line = fh.readline(1_000_000)
    text = line.decode("utf-8-sig", errors="replace").strip()
    return [c.strip().strip('"').strip().upper() for c in text.split(",")] if text else []


def read_log() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_log(row: dict) -> None:
    with _log_lock:
        new = not LOG_FILE.exists()
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG_COLUMNS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in LOG_COLUMNS})


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- pages
@app.get("/")
def index(user: str = Depends(current_user)):
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/config")
def config(user: str = Depends(current_user)):
    return {"user": user, "chunk_size": CHUNK, "file_map": FILE_MAP}


@app.get("/api/log")
def log(user: str = Depends(current_user)):
    """Upload history, plus the Snowflake result per file from pipeline_log.csv (step 2)."""
    sf: dict[str, dict] = {}
    pipe = DATA_DIR / "pipeline_log.csv"
    if pipe.exists():
        with open(pipe, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                sf[r["saved_path"]] = r          # last line per file wins
    rows = list(reversed(read_log()))[:500]
    for r in rows:
        p = sf.get(r.get("saved_path") or "")
        r["sf_status"] = p["status"] if p else ("WAITING" if r.get("status") == "SAVED" else "")
        r["sf_message"] = p["message"] if p else ""
    return rows


# --------------------------------------------------------------------------- upload API
@app.post("/api/upload/start")
async def start(request: Request, user: str = Depends(current_user)):
    """Body: {file_name, size, batch}. Returns an upload_id for sending the pieces."""
    body = await request.json()
    name, size, batch = str(body.get("file_name", "")), int(body.get("size", -1)), str(body.get("batch", ""))
    if not SAFE_NAME.match(name):
        raise HTTPException(400, "File name may only contain letters, digits, _ . - (no spaces). Rename it and try again.")
    if not ALLOWED_EXT.search(name):
        raise HTTPException(400, "Only .csv, .csv.gz or .csv_partN.gz files are accepted.")
    if not re.fullmatch(r"\d{8}_\d{6}", batch):
        raise HTTPException(400, "Bad batch id.")
    m = match_file(name)
    if m is None:
        raise HTTPException(400, "File name not recognised. It must start with one of the known names (see the list on the page).")
    if size <= 0:
        raise HTTPException(400, "The file is empty.")
    uid = secrets.token_hex(12)
    part = TMP / f"{uid}.part"
    part.touch()
    with _uploads_lock:
        _uploads[uid] = dict(user=user, name=name, size=size, batch=f"{batch}_{user}", map=m,
                             part=part, received=set(), started=now())
    return {"upload_id": uid, "chunk_size": CHUNK, "chunks": (size + CHUNK - 1) // CHUNK}


@app.put("/api/upload/{uid}/{index}")
async def piece(uid: str, index: int, request: Request, user: str = Depends(current_user)):
    """One piece of the file. Pieces can be re-sent (a retry just overwrites the same place)."""
    st = _uploads.get(uid)
    if st is None or st["user"] != user:
        raise HTTPException(404, "Unknown upload (the server may have restarted). Start the file again.")
    data = await request.body()
    offset = index * CHUNK
    if index < 0 or offset >= st["size"] or len(data) > CHUNK or offset + len(data) > st["size"]:
        raise HTTPException(400, "Piece out of range.")
    with open(st["part"], "r+b") as fh:
        fh.seek(offset)
        fh.write(data)
    st["received"].add(index)
    return {"ok": True}


@app.post("/api/upload/{uid}/finish")
def finish(uid: str, user: str = Depends(current_user)):
    """All pieces sent: check size, header and duplicates, then move into incoming/<batch>/."""
    with _uploads_lock:
        st = _uploads.pop(uid, None)
    if st is None or st["user"] != user:
        raise HTTPException(404, "Unknown upload.")
    part, m = st["part"], st["map"]
    row = dict(uploaded_at=now(), user=user, batch=st["batch"], file_name=st["name"],
               target_table=m["target_table"], size_bytes=st["size"])
    try:
        expected = (st["size"] + CHUNK - 1) // CHUNK
        if len(st["received"]) != expected or part.stat().st_size != st["size"]:
            raise ValueError(f"Incomplete upload: got {len(st['received'])} of {expected} pieces. Upload the file again.")

        sha = hashlib.sha256()
        with open(part, "rb") as fh:
            for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                sha.update(block)
        row["sha256"] = sha.hexdigest()

        got = header_of(part)
        if got != m["header"]:
            raise ValueError(f"Header does not match. Expected: {','.join(m['header'])} | Found: {','.join(got) or '(empty)'}")

        if any(r["sha256"] == row["sha256"] and r["status"] == "SAVED" for r in read_log()):
            part.unlink(missing_ok=True)
            row.update(status="DUPLICATE", message="This exact file was uploaded before, so it was not saved again.")
        else:
            dest_dir = INCOMING / st["batch"]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / st["name"]
            if dest.exists():
                dest = dest_dir / f"{dest.stem}_{secrets.token_hex(3)}{dest.suffix}"
            shutil.move(str(part), dest)
            row.update(status="SAVED", message=f"Saved for table {m['target_table']}",
                       saved_path=str(dest.relative_to(DATA_DIR)).replace("\\", "/"))
    except Exception as e:  # any problem: keep nothing, record why
        part.unlink(missing_ok=True)
        row.update(status="REJECTED", message=str(e))
    write_log(row)
    return JSONResponse(row)


@app.delete("/api/upload/{uid}")
def cancel(uid: str, user: str = Depends(current_user)):
    with _uploads_lock:
        st = _uploads.pop(uid, None)
    if st:
        st["part"].unlink(missing_ok=True)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    if USERS == {"dialog": "change-me"}:
        print("WARNING: using the default login dialog / change-me. Set UPLOAD_USERS before sharing the page.")
    print(f"Files will be saved in: {INCOMING}")
    # HOST=127.0.0.1 when a web server (Caddy / Nginx) in front handles HTTPS
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
