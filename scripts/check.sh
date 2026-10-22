#!/usr/bin/env sh
# Runs every check required before merging (DEV_CONVENTIONS). Needs the db container running:
#   docker compose up -d db
set -eu
cd "$(dirname "$0")/.."

echo "== backend"
docker compose run --rm backend sh -c "ruff check . && ruff format --check . && mypy . && pytest"

echo "== frontend"
docker compose run --rm --no-deps frontend sh -c "npm run typecheck && npm run lint && npm test"

echo "All checks passed"
