#!/usr/bin/env sh
# Audit item 27: a copy counts only when it is known to come back. Weekly (deploy/backup-timer.sh) the
# server makes a fresh copy, restores it into a temporary database next to the working one and
# compares them:
#   - the checksums of the copy;
#   - the database restores without an error;
#   - the same tables and the same applied migrations;
#   - the same rows table by table, except what changed during the minute between the copy and the
#     comparison: at most 1 % of all rows and 20 rows, whichever is more (PROPOSAL);
#   - the archive of files unpacks and holds as many files as the volume.
# Only then the check is marked done (heartbeat restore_check); a week without it shows on
# /api/health/full, which the outside monitor watches. The working database is only read.
#   ./scripts/restore-check.sh
set -eu
cd "$(dirname "$0")/.."

DIR="${C4R_BACKUP_DIR:-/var/backups/c4rsys}"
compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"
work="$(mktemp -d)"
cleanup() {
  $compose exec -T db dropdb -U c4rsys --if-exists restore_check < /dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

./scripts/backup.sh
copy="$(ls -d "$DIR"/20* | tail -n 1)"
echo "Checking $copy"
(cd "$copy" && sha256sum --quiet -c SHA256SUMS)

$compose exec -T db dropdb -U c4rsys --if-exists restore_check < /dev/null
$compose exec -T db createdb -U c4rsys restore_check < /dev/null
$compose exec -T db pg_restore -U c4rsys -d restore_check --no-owner --exit-on-error < "$copy/db.dump"

counts() {
  $compose exec -T db psql -X -q -At -U c4rsys -d "$1" < deploy/table-counts.sql
}
counts c4rsys > "$work/live"
counts restore_check > "$work/copy"
migrations="select app || '.' || name from django_migrations order by 1"
$compose exec -T db psql -X -At -U c4rsys -d c4rsys -c "$migrations" < /dev/null > "$work/live-migrations"
$compose exec -T db psql -X -At -U c4rsys -d restore_check -c "$migrations" < /dev/null > "$work/copy-migrations"

cut -d'|' -f1 "$work/live" > "$work/live-tables"
cut -d'|' -f1 "$work/copy" > "$work/copy-tables"
cmp -s "$work/live-tables" "$work/copy-tables" || { echo "FAILED: the tables differ"; exit 1; }
cmp -s "$work/live-migrations" "$work/copy-migrations" || { echo "FAILED: the migrations differ"; exit 1; }
summary="$(paste -d'|' "$work/live" "$work/copy" | awk -F'|' '
  { live += $2; copy += $4
    if ($2 != $4) { changed++; diff += ($2 > $4 ? $2 - $4 : $4 - $2); names = names " " $1 } }
  END {
    allowed = live / 100; if (allowed < 20) allowed = 20
    printf "%d tables, %d rows live, %d rows restored, %d tables changed meanwhile by %d rows:%s",
      NR, live, copy, changed, diff, names
    if (copy == 0 || diff > allowed) exit 1
  }')" || { echo "FAILED: $summary"; exit 1; }
echo "$summary"

archived="$(tar -tzf "$copy/files.tar.gz" | grep -vc '/$' || true)"
present="$($compose exec -T backend find /data/files -type f < /dev/null | grep -c . || true)"
echo "files: $archived in the copy, $present in the volume"
[ "$archived" -eq "$present" ] || { echo "FAILED: the files differ"; exit 1; }

$compose exec -T backend python manage.py heartbeat restore_check < /dev/null
echo "Restore check passed"
