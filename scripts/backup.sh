#!/usr/bin/env sh
# Backup of the test server (audit item 26): the database and the uploaded files, together.
# Run on the server in the repository folder (the systemd timer from deploy/backup-timer.sh does it daily):
#   ./scripts/backup.sh
# Each run makes C4R_BACKUP_DIR/<UTC time>/ with db.dump (pg_dump custom format), files.tar.gz and
# SHA256SUMS, and COMMIT, the version deployed when it was made; scripts/deploy.sh names its copies
# <UTC time>-before-<version> (audit item 24). The dump is checked with pg_restore --list before
# the copy counts as made. Copies older than C4R_BACKUP_KEEP_DAYS are removed. .env.prod is not
# copied: its secrets are kept apart.
# PROPOSAL (агент, не подтверждено): every day, 14 days, on the same server (O13 is OPEN).
set -eu
cd "$(dirname "$0")/.."

DIR="${C4R_BACKUP_DIR:-/var/backups/c4rsys}"
KEEP_DAYS="${C4R_BACKUP_KEEP_DAYS:-14}"
compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"
deployed="$(tail -n 1 .deploy-history 2>/dev/null || true)"
# The image of a one-off backend below: the deployed version unless scripts/deploy.sh gives its own.
[ -z "$deployed" ] || { C4R_VERSION="${C4R_VERSION:-$deployed}"; export C4R_VERSION; }

umask 077
mkdir -p "$DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)${C4R_BACKUP_LABEL:+-$C4R_BACKUP_LABEL}"
work="$DIR/.$stamp.partial"
rm -rf "$work"
mkdir "$work"
trap 'rm -rf "$work"' EXIT
# A backend that does not run (its new version failed to start) is replaced by a one-off container.
backend() {
  if [ -n "$($compose ps --status running -q backend)" ]; then
    $compose exec -T backend "$@"
  else
    $compose run --rm --no-deps -T backend "$@"
  fi
}

$compose exec -T db pg_dump -U c4rsys -d c4rsys --format=custom --no-owner > "$work/db.dump"
# pg_restore reads the whole table of contents: a cut or broken dump fails here.
$compose exec -T db pg_restore --list < "$work/db.dump" > /dev/null
backend tar -C /data/files -czf - . > "$work/files.tar.gz"
gzip -t "$work/files.tar.gz"
echo "${deployed:-$(git rev-parse HEAD)}" > "$work/COMMIT"
(cd "$work" && sha256sum db.dump files.tar.gz COMMIT > SHA256SUMS)

mv "$work" "$DIR/$stamp"
trap - EXIT
find "$DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$KEEP_DAYS" -exec rm -rf {} +
echo "Backup $DIR/$stamp: $(du -sh "$DIR/$stamp" | cut -f1)"
# Audit item 27: the copy is marked made; a day without one shows on /api/health/full.
backend python manage.py heartbeat backup < /dev/null \
  || echo "The copy is made, but it could not be marked on /api/health/full"

# The second copy off the server to S3 storage. Customer decision 03.11.2026: postponed on the test
# server, copies stay local; the code is ready for Timeweb S3. Settings are in .env.backup (template: deploy/env.backup.example), apart from .env.prod, so the
# application containers never see the storage keys. Without the file the copy stays local only.
[ -f .env.backup ] || { echo "No .env.backup: the copy is not sent off the server"; exit 0; }
setting() { grep -E "^$1=" .env.backup | tail -1 | cut -d= -f2-; }
S3_ENDPOINT="$(setting C4R_BACKUP_S3_ENDPOINT)"
S3_BUCKET="$(setting C4R_BACKUP_S3_BUCKET)"
AWS_DEFAULT_REGION="$(setting C4R_BACKUP_S3_REGION)"
AWS_ACCESS_KEY_ID="$(setting C4R_BACKUP_S3_ACCESS_KEY)"
AWS_SECRET_ACCESS_KEY="$(setting C4R_BACKUP_S3_SECRET_KEY)"
export AWS_DEFAULT_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
# The keys reach the container through the environment, never through the command line.
# 2.17: newer versions send checksums that S3-compatible storages do not all accept.
s3() {
  docker run --rm -e AWS_DEFAULT_REGION -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    -v "$DIR:/backups:ro" amazon/aws-cli:2.17.0 --endpoint-url "$S3_ENDPOINT" "$@"
}
s3 s3 cp --recursive --only-show-errors "/backups/$stamp" "s3://$S3_BUCKET/$stamp/"
echo "Sent to S3: $(s3 s3 ls "s3://$S3_BUCKET/$stamp/" | grep -c .) files in $S3_BUCKET/$stamp"
# Stamps sort as text in time order, so older copies are those whose name is below the border.
border="$(date -u -d "-$KEEP_DAYS days" +%Y%m%dT%H%M%SZ)"
s3 s3 ls "s3://$S3_BUCKET/" \
  | awk -v border="$border" '$1 == "PRE" { sub("/$", "", $2); if ($2 ~ /^20/ && $2 < border) print $2 }' \
  | while read -r old; do
      s3 s3 rm --recursive --only-show-errors "s3://$S3_BUCKET/$old/"
      echo "Removed from S3: $old"
    done
