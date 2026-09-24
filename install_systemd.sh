#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_USER="${SUDO_USER:-$USER}"
if [ "$(id -u)" -ne 0 ]; then
    echo "Run: sudo ./install_systemd.sh"
    exit 1
fi
SERVICE_USER="${SERVICE_USER:-samir}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
PYTHON="$APP_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "Missing $PYTHON"
    exit 1
fi
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$APP_DIR/data" "$APP_DIR/credentials" "$APP_DIR/logs" 2>/dev/null || true
chmod 700 "$APP_DIR/data" "$APP_DIR/credentials" "$APP_DIR/credentials/tokens" "$APP_DIR/logs" 2>/dev/null || true
[ ! -e "$APP_DIR/data/.session_secret" ] || chmod 600 "$APP_DIR/data/.session_secret" 2>/dev/null || true
cat > /etc/systemd/system/youtube-uploader.service <<EOF
[Unit]
Description=YouTube Local Auto Uploader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
Environment=HOME=/home/$SERVICE_USER
Environment=USER=$SERVICE_USER
Environment=LOGNAME=$SERVICE_USER
Environment=HOST=0.0.0.0
Environment=PORT=8080
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON $APP_DIR/run.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable youtube-uploader.service
systemctl restart youtube-uploader.service
systemctl --no-pager --full status youtube-uploader.service || true
echo
echo "Service installed for: $APP_DIR"
echo "Open: http://$(hostname -I | awk '{print $1}'):8080"
