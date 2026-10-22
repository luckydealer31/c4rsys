#!/usr/bin/env sh
# End-to-end checks (T19, acceptance criteria 1 and 8): the base example in a browser on a
# separate stack with its own database, removed afterwards. Uses the images of the development
# stack when they exist.
set -eu
cd "$(dirname "$0")/.."

compose="docker compose -p c4rsys-e2e -f docker-compose.yml -f docker-compose.e2e.yml"
trap '$compose --profile e2e down -v --remove-orphans' EXIT

for service in backend frontend; do
  docker image inspect "c4rsys-$service" >/dev/null 2>&1 || $compose build "$service"
done

echo "== e2e"
$compose up -d db backend frontend
$compose --profile e2e run --rm e2e
echo "E2E checks passed"
