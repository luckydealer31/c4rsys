#!/usr/bin/env sh
# Backup of the test server (audit item 26): the database and the uploaded files, together.
# Run on the server in the repository folder (the systemd timer from deploy/backup-timer.sh does it daily):
#   ./scripts/backup.sh
# Each run makes C4R_BACKUP_DIR/<UTC time>/ with db.dump (pg_dump custom format), files.tar.gz and
# SHA256SUMS; the dump is checked with pg_restore --list before the copy counts as made. Copies older
# than C4R_BACKUP_KEEP_DAYS are removed. .env.prod is not copied: its secrets are kept apart.
# PROPOSAL (агент, не подтверждено): every day, 14 days, on the same server (O13 is OPEN).
set -eu
cd "$(dirname "$0")/.."

DIR="${C4R_BACKUP_DIR:-/var/backups/c4rsys}"
KEEP_DAYS="${C4R_BACKUP_KEEP_DAYS:-14}"
compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"

umask 077
mkdir -p "$DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work="$DIR/.$stamp.partial"
rm -rf "$work"
mkdir "$work"
trap 'rm -rf "$work"' EXIT

$compose exec -T db pg_dump -U c4rsys -d c4rsys --format=custom --no-owner > "$work/db.dump"
# pg_restore reads the whole table of contents: a cut or broken dump fails here.
$compose exec -T db pg_restore --list < "$work/db.dump" > /dev/null
$compose exec -T backend tar -C /data/files -czf - . > "$work/files.tar.gz"
gzip -t "$work/files.tar.gz"
git rev-parse HEAD > "$work/COMMIT"
(cd "$work" && sha256sum db.dump files.tar.gz COMMIT > SHA256SUMS)

mv "$work" "$DIR/$stamp"
trap - EXIT
find "$DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$KEEP_DAYS" -exec rm -rf {} +
echo "Backup $DIR/$stamp: $(du -sh "$DIR/$stamp" | cut -f1)"
