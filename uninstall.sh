#!/usr/bin/env bash
# Dezinstalare astra_web (pastreaza settings.json/channels.json in /opt/astra-web).
set -e
if [ "$(id -u)" -ne 0 ]; then echo "Ruleaza cu sudo"; exit 1; fi
systemctl disable --now astra-web 2>/dev/null || true
rm -f /etc/systemd/system/astra-web.service
systemctl daemon-reload
echo "Serviciul a fost scos. Fisierele raman in /opt/astra-web (sterge-le manual daca vrei)."
