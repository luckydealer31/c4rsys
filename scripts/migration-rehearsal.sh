#!/usr/bin/env sh
# Audit item 46: the migrations of a new version are tried on a copy of the real data before they
# touch the working database. The copy (scripts/backup.sh) is restored into a temporary database
# next to the working one, owned by the application role as the working one is (audit item 38),
# and the images of C4R_VERSION migrate it as the backend does when it starts (Dockerfile.prod:
# no time limit on queries).
# The working database is not touched; the temporary one is removed in any case. Run by
# scripts/deploy.sh before a version with migrations; by hand on the server:
#   C4R_VERSION=<commit> ./scripts/migration-rehearsal.sh /var/backups/c4rsys/<UTC time>
# Prints how long the migrations took: the backend of the new version needs about as long at
# the deploy before it answers.
set -eu
cd "$(dirname "$0")/.."

copy="${1:?give the folder of a copy of scripts/backup.sh}"
[ -f "$copy/db.dump" ] || { echo "No db.dump in $copy"; exit 1; }
compose="${C4R_COMPOSE:-docker compose -f docker-compose.prod.yml --env-file .env.prod}"
name=migration_rehearsal

drop() {
  $compose exec -T db dropdb -U c4rsys --force --if-exists "$name" < /dev/null
}
trap drop EXIT

drop
$compose exec -T db createdb -U c4rsys -O c4rsys_app "$name" < /dev/null
# --role: the restored objects belong to the application role, which runs the migrations.
$compose exec -T db pg_restore -U c4rsys -d "$name" --no-owner --role=c4rsys_app --exit-on-error \
  < "$copy/db.dump"

# The address of the temporary database is made inside the container from the backend's own
# DATABASE_URL: the password never appears on a command line here.
started="$(date +%s)"
$compose run --rm --no-deps -T --entrypoint sh backend -c \
  'export DATABASE_URL="${DATABASE_URL%/*}/'"$name"'" C4R_DB_STATEMENT_TIMEOUT_MS=0 C4R_DB_IDLE_TRANSACTION_TIMEOUT_MS=0; python manage.py migrate --noinput && python manage.py migrate --check --noinput' \
  < /dev/null
echo "Migrations rehearsed on $copy in $(( $(date +%s) - started )) s"
