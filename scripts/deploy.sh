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

# Audit item 38: the password of the application database role, made once and never printed.
if ! grep -q '^C4R_DB_APP_PASSWORD=.' .env.prod; then
  sed -i '/^C4R_DB_APP_PASSWORD=/d' .env.prod
  echo "C4R_DB_APP_PASSWORD=$(openssl rand -hex 24)" >> .env.prod
fi

compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"
$compose build
# Audit item 38: Caddy runs as caddy (uid 1000); volumes made while it ran as root are handed over.
$compose run --rm --no-deps -T --user root --cap-add CHOWN --entrypoint chown web -R 1000:1000 /data /config
$compose up -d --wait
$compose ps
echo "Deployed $(git rev-parse --short HEAD)"
