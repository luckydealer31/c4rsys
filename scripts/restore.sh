#!/usr/bin/env sh
# Audit item 49: puts the database and the uploaded files back from a copy of scripts/backup.sh.
# Everything made after that copy is lost, so the current state is copied first. The site answers
# with errors for a minute or two while the services are stopped. Run on the server:
#   ./scripts/restore.sh /var/backups/c4rsys/<UTC time>
# It asks to type RESTORE; C4R_RESTORE_CONFIRM=RESTORE answers for a drill without a terminal.
set -eu
cd "$(dirname "$0")/.."

copy="${1:?give the folder of a copy, for example $(ls -d /var/backups/c4rsys/20* 2>/dev/null | tail -n 1)}"
[ -f "$copy/db.dump" ] && [ -f "$copy/files.tar.gz" ] || { echo "No copy in $copy"; exit 1; }
(cd "$copy" && sha256sum --quiet -c SHA256SUMS)

answer="${C4R_RESTORE_CONFIRM:-}"
if [ -z "$answer" ]; then
  printf 'The database and the files will be replaced by the copy %s.\nType RESTORE to go on: ' "$copy"
  read -r answer
fi
[ "$answer" = "RESTORE" ] || { echo "Nothing changed"; exit 1; }

# The images of the version on the server now (scripts/deploy.sh).
version="$(tail -n 1 .deploy-history 2>/dev/null || git rev-parse HEAD)"
C4R_VERSION="$version"
C4R_WEB_VERSION="tree-$(git rev-parse "$version:frontend")"
C4R_RELEASE="$(echo "$version" | cut -c1-7)"
export C4R_VERSION C4R_WEB_VERSION C4R_RELEASE
compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"

echo "== a copy of the current state first"
./scripts/backup.sh || echo "The current state could not be copied; going on with the restore"

echo "== stopping the application"
$compose stop backend rates deposits schedule

echo "== database"
$compose exec -T db dropdb -U c4rsys --force --if-exists c4rsys < /dev/null
$compose exec -T db createdb -U c4rsys c4rsys < /dev/null
$compose exec -T db pg_restore -U c4rsys -d c4rsys --no-owner --exit-on-error < "$copy/db.dump"

echo "== files"
$compose run --rm --no-deps -T --entrypoint sh backend \
  -c 'find /data/files -mindepth 1 -delete && tar -C /data/files -xzf -' < "$copy/files.tar.gz"

echo "== starting the application"
# db-role gives the restored tables back to the application role (audit item 38).
$compose up -d --no-build --wait --no-deps db-role backend rates deposits schedule
echo "Restored from $copy"
