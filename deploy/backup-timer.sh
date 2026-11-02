#!/usr/bin/env bash
# Installs the daily backup of the test server as a systemd timer (audit item 26). Run once as root:
#   bash deploy/backup-timer.sh
# The time is 20:00 UTC (03:00 in Thailand), PROPOSAL (агент, не подтверждено). Safe to run again.
# Status: systemctl list-timers c4rsys-backup; log: journalctl -u c4rsys-backup.
set -euo pipefail

DIR="${C4R_DIR:-/opt/c4rsys}"
[ "$(id -u)" -eq 0 ] || { echo "Run as root"; exit 1; }

cat > /etc/systemd/system/c4rsys-backup.service <<EOF
[Unit]
Description=c4rsys backup of the database and files
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/scripts/backup.sh
EOF

cat > /etc/systemd/system/c4rsys-backup.timer <<'EOF'
[Unit]
Description=Daily c4rsys backup

[Timer]
OnCalendar=*-*-* 20:00:00 UTC
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now c4rsys-backup.timer
systemctl list-timers c4rsys-backup.timer --no-pager
