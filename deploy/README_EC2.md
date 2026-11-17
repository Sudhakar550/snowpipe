# Step 1: the upload page on AWS EC2

Result: `https://<server IP>` with a login, files kept in `/data/dialog_uploads/incoming/`.
Time: about 30 minutes. The full runbook (all commands) is in `../README.md`.

Current server: Elastic IP **3.26.129.230**, user `ubuntu`, key `dialog-key.pem`.

---

## 1. Launch the server (AWS console)

EC2 → **Launch instance**

| Setting | Choose |
|---|---|
| Region | Same as Snowflake if possible (Snowsight: `SELECT CURRENT_REGION();`) |
| Name | `dialog-upload` |
| Image | **Ubuntu Server 24.04 LTS** |
| Instance type | **t3.small** |
| Key pair | Create `dialog-key`, RSA, **.pem**. Keep the file safe: it is the only way in. |
| Network | **SSH** from My IP, **HTTP** from Anywhere, **HTTPS** from Anywhere |
| Storage | **50 GB gp3** |

## 2. Fixed address (Elastic IP)

EC2 → **Elastic IPs** → **Allocate** → select it → **Actions → Associate** → the instance.

## 3. Connect (PowerShell on the laptop)

```powershell
icacls "$env:USERPROFILE\Downloads\dialog-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i "$env:USERPROFILE\Downloads\dialog-key.pem" ubuntu@3.26.129.230
```

Or in the browser: EC2 → instance → **Connect** → **EC2 Instance Connect**.

## 4. Get the code and install

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Sudhakar550/snowpipe.git ~/snowpipe     # private repo: use a GitHub token as the password
cd ~/snowpipe
sudo bash deploy/setup_ec2.sh
```

It asks:

1. **Web address.** Press Enter to use the server IP. This works everywhere, including behind
   the office firewall, which blocks sslip.io addresses. The browser shows "Not secure" once:
   click **Advanced → Continue**. The connection is still encrypted.
   Type a domain instead (e.g. `dialog-upload.thbs.com`) only after IT has pointed it at the
   Elastic IP; then the certificate is trusted and there is no warning.
   On a re-run, Enter keeps the address used last time.
2. **Login name** (Enter = `dialog`) and **password** (10+ characters, no `:` or `,`).
   Asked only the first time; later runs keep the logins.

It installs Python, Caddy and the app in `/opt/dialog-upload`, creates the service
`dialog-upload`, and ends with `DONE. Upload page: https://3.26.129.230`.

## 5. Check

Open the address → **Advanced → Continue** → sign in. You should see the upload page.

```bash
sudo systemctl is-active dialog-upload caddy      # active, active
```

## Next

Connect the server to Snowflake: `deploy/README_PIPELINE.md`.

## Commands

| Task | Command |
|---|---|
| Install a new version of the code | `cd ~/snowpipe && git pull && sudo bash deploy/update.sh` |
| View logins | `sudo grep UPLOAD_USERS /etc/dialog-upload.env` |
| Change logins | `sudo nano /etc/dialog-upload.env`, then `sudo systemctl restart dialog-upload` |
| Status | `sudo systemctl status dialog-upload caddy --no-pager` |
| Errors | `sudo journalctl -u dialog-upload -n 50 --no-pager -l` |
| Web address settings | `cat /etc/caddy/Caddyfile` |
| Uploaded files | `ls -lh /data/dialog_uploads/incoming/*/` |
| Disk space | `df -h /` |
| Copy uploads to the laptop (PowerShell) | `scp -i "$env:USERPROFILE\Downloads\dialog-key.pem" -r ubuntu@3.26.129.230:/data/dialog_uploads/incoming .` |

## Cost (approximate)

t3.small about $15–20 a month, 50 GB disk about $4–5, Elastic IP about $3.6.
EC2 → **Instance state → Stop** when not needed: files are kept, the address stays the same.

## Security

- HTTPS only; the login is sent encrypted.
- SSH is open only to your IP. If your IP changes: EC2 → Security groups → Edit inbound rules → SSH → My IP.
- Logins are in `/etc/dialog-upload.env` (readable only by root and the app). Don't post them in chat or commit them.
