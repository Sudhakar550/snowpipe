# Put the upload page on AWS EC2

Result: a page at `https://<address>` that anyone with a login can open, files kept on
the server in `/data/dialog_uploads/incoming/`, and Snowflake CLI ready on the same server
(no Windows laptop needed for the Snowflake step later).

Time: about 30 minutes. You need an AWS account with permission to create EC2 instances.

---

## 1. Launch the server (AWS console)

AWS console → **EC2** → **Launch instance**

| Setting | Choose |
|---|---|
| Region (top right) | Same region as Snowflake if possible. In Snowsight run `SELECT CURRENT_REGION();` (e.g. `AWS_AP_SOUTH_1` = Mumbai `ap-south-1`) |
| Name | `dialog-upload` |
| Image (AMI) | **Ubuntu Server 24.04 LTS** |
| Instance type | **t3.small** (2 GB memory) |
| Key pair | **Create new key pair** → name `dialog-key`, type RSA, format **.pem** → it downloads `dialog-key.pem`. Keep it safe: it is the only way in. |
| Network settings → Edit | Allow **SSH** from **My IP**, allow **HTTPS** from Anywhere, allow **HTTP** from Anywhere (needed for the free certificate) |
| Storage | **50 GB gp3** (uploaded files are kept on this disk) |

Click **Launch instance**.

## 2. Give it a fixed address (Elastic IP)

EC2 → **Elastic IPs** → **Allocate Elastic IP address** → Allocate →
select it → **Actions → Associate** → choose the `dialog-upload` instance → Associate.

Note the address, e.g. `13.234.56.78`. Without this the address changes on every restart.

## 3. Connect from your Windows laptop

Easiest: EC2 → select the instance → **Connect** → **EC2 Instance Connect** → **Connect**
(a terminal opens in the browser, nothing to install).

Or from Windows PowerShell (built into Windows 10/11):

```powershell
# one time: make the key private (Windows OpenSSH refuses keys others can read)
icacls "$env:USERPROFILE\Downloads\dialog-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"

ssh -i "$env:USERPROFILE\Downloads\dialog-key.pem" ubuntu@13.234.56.78
```

## 4. Copy the app to the server and install it

From **PowerShell on your laptop** (replace the IP):

```powershell
scp -i "$env:USERPROFILE\Downloads\dialog-key.pem" "$env:USERPROFILE\Downloads\shared_data\upload_app.zip" ubuntu@13.234.56.78:~
```

(With EC2 Instance Connect instead: put the zip somewhere it can be downloaded, or use
the "Upload file" option of AWS CloudShell. scp is simplest.)

Then **on the server**:

```bash
sudo apt-get install -y unzip
unzip -o upload_app.zip
cd upload_app
sudo bash deploy/setup_ec2.sh
```

It asks three things:

1. **Web address**: press Enter to accept `13-234-56-78.sslip.io` (works straight away), or type
   your company domain if IT has pointed one at the Elastic IP (e.g. `upload.thbs.com`).
2. **Login name**: press Enter for `dialog`.
3. **Password**: at least 10 characters (you will give this to Dialog).

At the end it prints the link, e.g. **https://13-234-56-78.sslip.io**.

## 5. Use it

- Open the link, sign in, upload files. Give Dialog the same link and login.
- Uploaded files: `/data/dialog_uploads/incoming/<date_time>_<user>/`
- Log of every upload: `/data/dialog_uploads/upload_log.csv`

## Everyday commands (on the server)

| Task | Command |
|---|---|
| Is it running? | `sudo systemctl status dialog-upload` |
| Restart | `sudo systemctl restart dialog-upload` |
| See errors | `sudo journalctl -u dialog-upload -n 50` |
| Add or change logins | `sudo nano /etc/dialog-upload.env` → edit `UPLOAD_USERS=dialog:pass1,thbs:pass2` → save → restart |
| See uploaded files | `ls -lh /data/dialog_uploads/incoming/*` |
| Disk space left | `df -h /` |
| Install a new version | copy the new zip, `unzip -o upload_app.zip`, `cd upload_app`, `sudo bash deploy/setup_ec2.sh` (logins and files are kept) |

## Snowflake CLI on the server

Already installed by the setup (`snow --version`). For the later automatic push to Snowflake
we will add a connection on the server (account `COUTGXW-TK77861`); best practice there is a
key-pair login for a dedicated Snowflake user instead of a personal password. That is part of
the next step.

## Copy files from the server to your laptop (if ever needed)

```powershell
scp -i "$env:USERPROFILE\Downloads\dialog-key.pem" -r ubuntu@13.234.56.78:/data/dialog_uploads/incoming .
```

## Cost (approximate, varies by region)

| Item | Per month |
|---|---|
| t3.small running all the time | about $15–20 |
| 50 GB gp3 disk | about $4–5 |
| Elastic IP (public IPv4) | about $3.6 |

**Stop** the instance when not needed (EC2 → Instance state → Stop): compute stops costing,
the disk and Elastic IP still cost a little, files are kept. Start it again and the same link works.

## Security notes

- The page is HTTPS only; the login is sent encrypted.
- SSH is open only to your IP. If your IP changes, update the security group (EC2 → Security groups → Edit inbound rules → SSH → My IP).
- `sslip.io` is a free public name service that turns the IP into a host name. For production,
  ask IT for a company sub-domain (for example `dialog-upload.thbs.com`) pointing to the Elastic IP,
  and run the setup again with that name.
