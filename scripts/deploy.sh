#!/usr/bin/env sh
# Updates the test server to the current main (T20). Run on the server in the repository folder:
#   ./scripts/deploy.sh
# Needs .env.prod (template: deploy/env.prod.example). Migrations run when the backend starts.
set -eu
cd "$(dirname "$0")/.."

[ -f .env.prod ] || { echo ".env.prod is missing: copy deploy/env.prod.example and fill it in"; exit 1; }

git fetch origin main
git checkout -q main
git merge --ff-only origin/main

compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"
$compose up -d --build --wait
$compose ps
echo "Deployed $(git rev-parse --short HEAD)"
