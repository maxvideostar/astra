#!/usr/bin/env bash
# Instalare astra_web pe Ubuntu/Debian ca serviciu systemd.
# Rulare:  sudo ./install.sh
# Optional: PORT=9000 sudo -E ./install.sh
set -e

DEST="/opt/astra-web"
PORT="${PORT:-8080}"
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ruleaza cu sudo:  sudo ./install.sh"; exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "Instalez python3..."; apt-get update -y && apt-get install -y python3
fi

echo "==> Copiez in $DEST"
mkdir -p "$DEST"
cp "$SRC/astra_web.py" "$DEST/astra_web.py"

if [ -f "$SRC/astra" ]; then
    cp "$SRC/astra" "$DEST/astra"
    chmod +x "$DEST/astra"
    ASTRA_ARG="--astra $DEST/astra"
    echo "==> Binar astra inclus."
else
    ASTRA_ARG=""
    echo "==> Binarul 'astra' nu e in folder; seteaza calea din interfata (Setari)."
fi

echo "==> Creez serviciul systemd astra-web"
cat > /etc/systemd/system/astra-web.service <<EOF
[Unit]
Description=astra_web - interfata web pentru Astra Cesbo
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEST
ExecStart=/usr/bin/python3 $DEST/astra_web.py $ASTRA_ARG --port $PORT --bind 0.0.0.0
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now astra-web

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "================================================"
echo " astra_web pornit."
echo " Interfata:  http://${IP:-<ip-server>}:$PORT   (user: admin / parola: admin)"
echo " Status:     systemctl status astra-web"
echo " Loguri:     journalctl -u astra-web -f"
echo " Oprire:     systemctl stop astra-web"
echo "================================================"
