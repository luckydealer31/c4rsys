#!/usr/bin/env bash
# Installs the systemd timers of the test server. Run once as root, and again after a change here:
#   bash deploy/backup-timer.sh
# - c4rsys-backup: the daily copy (audit item 26), 20:00 UTC — 03:00 in Thailand;
# - c4rsys-restore-check: the weekly restore of a fresh copy (audit item 27), Sunday 21:00 UTC —
#   Monday 04:00 in Thailand.
# The times are PROPOSAL (агент, не подтверждено). Safe to run again.
# Status: systemctl list-timers 'c4rsys-*'; log: journalctl -u c4rsys-backup -u c4rsys-restore-check.
set -euo pipefail

DIR="${C4R_DIR:-/opt/c4rsys}"
[ "$(id -u)" -eq 0 ] || { echo "Run as root"; exit 1; }

unit() {  # name, description, script, calendar
  cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=$2
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/scripts/$3
EOF
  cat > "/etc/systemd/system/$1.timer" <<EOF
[Unit]
Description=$2 on schedule

[Timer]
OnCalendar=$4
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
}

unit c4rsys-backup "c4rsys backup of the database and files" backup.sh "*-*-* 20:00:00 UTC"
unit c4rsys-restore-check "c4rsys restore check of a fresh backup" restore-check.sh "Sun *-*-* 21:00:00 UTC"

systemctl daemon-reload
systemctl enable --now c4rsys-backup.timer c4rsys-restore-check.timer
systemctl list-timers 'c4rsys-*' --no-pager
