#!/usr/bin/env sh
# End-to-end checks (T19, acceptance criteria 1 and 8): the base example in a browser on a
# separate stack with its own database, removed afterwards.
#   ./scripts/e2e.sh        development stack (Vite, runserver); reuses its images
#   ./scripts/e2e.sh prod   the test server build (Caddy, gunicorn, DEBUG off), checked locally
set -eu
cd "$(dirname "$0")/.."

if [ "${1:-}" = "prod" ]; then
  export POSTGRES_PASSWORD=local-check-only
  compose="docker compose -p c4rsys-prodcheck -f docker-compose.prod.yml -f deploy/prod-check.yml"
  trap '$compose --profile e2e down -v --remove-orphans' EXIT
  echo "== e2e on the test server build"
  $compose up -d --build --wait db backend web
else
  compose="docker compose -p c4rsys-e2e -f docker-compose.yml -f docker-compose.e2e.yml"
  trap '$compose --profile e2e down -v --remove-orphans' EXIT
  for service in backend frontend; do
    docker image inspect "c4rsys-$service" >/dev/null 2>&1 || $compose build "$service"
  done
  echo "== e2e"
  $compose up -d db backend frontend
fi
$compose --profile e2e run --rm e2e
echo "E2E checks passed"
