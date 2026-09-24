# Systemd installation

This build is designed to run as the normal desktop user, not as root.

## First run

```bash
cd /path/to/YouTubeLocalAutoUploader
./start.sh
```

Do **not** use `sudo sh start.sh`.

## Install systemd

```bash
cd /path/to/YouTubeLocalAutoUploader
sudo ./install_systemd.sh
```

The installer uses the actual application directory, repairs ownership of `data/`, `credentials/`, and `logs/`, and creates `/etc/systemd/system/youtube-uploader.service`.

## Check

```bash
sudo systemctl status youtube-uploader
journalctl -u youtube-uploader -f
```

The web UI can be opened from another LAN device at `http://SERVER_IP:8080`.

## Session secret

The application no longer depends on a desktop keyring or a root-owned `data/.session_secret`. The canonical per-user session secret is stored at:

`~/.config/youtube-local-auto-uploader/session_secret`

with mode `0600`. A readable legacy `data/.session_secret` is migrated automatically.

## Important

Do not run the application with `sudo`, because that can create root-owned SQLite, credential, token, log, or session files that the `samir` service cannot read/write.
