#!/usr/bin/env sh
# Updates the test server (T20). Run on the server in the repository folder:
#   ./scripts/deploy.sh               the current main
#   ./scripts/deploy.sh previous      back to the version deployed before the current one
#   ./scripts/deploy.sh <commit>      any commit of main whose images CI has built
# Audit items 25 and 47: the server builds nothing. CI publishes the images of every commit of
# main (ghcr.io, .github/workflows/ci.yml); this script waits for them, downloads them and
# restarts the services; Caddy holds the requests while the backend restarts. The versions put on
# the server are listed in .deploy-history. Migrations run when the backend starts; going back
# does not undo them, so a version older than a migration that removed something may not start.
# Needs .env.prod (template: deploy/env.prod.example).
set -eu
cd "$(dirname "$0")/.."

[ -f .env.prod ] || { echo ".env.prod is missing: copy deploy/env.prod.example and fill it in"; exit 1; }

# The scripts and settings of the server always come from the current main.
git fetch -q origin main
git checkout -q main
git merge -q --ff-only origin/main

case "${1:-}" in
  "") version="$(git rev-parse HEAD)" ;;
  previous)
    version="$(tail -n 2 .deploy-history 2>/dev/null | head -n 1)"
    [ -n "$version" ] && [ "$(wc -l < .deploy-history)" -ge 2 ] \
      || { echo "No earlier version in .deploy-history"; exit 1; }
    ;;
  *) version="$(git rev-parse --verify "$1^{commit}")" ;;
esac

# Audit item 38: the password of the application database role, made once and never printed.
if ! grep -q '^C4R_DB_APP_PASSWORD=.' .env.prod; then
  sed -i '/^C4R_DB_APP_PASSWORD=/d' .env.prod
  echo "C4R_DB_APP_PASSWORD=$(openssl rand -hex 24)" >> .env.prod
fi

C4R_VERSION="$version"
# The version of the code in the reports of errors (audit item 21).
C4R_RELEASE="$(echo "$version" | cut -c1-7)"
export C4R_VERSION C4R_RELEASE
compose="docker compose -f docker-compose.prod.yml --env-file .env.prod"

# CI needs a few minutes after a push to publish the images.
tries=0
until $compose pull -q backend web; do
  tries=$((tries + 1))
  [ "$tries" -lt 40 ] || { echo "No images of $C4R_RELEASE after 20 minutes: is CI green?"; exit 1; }
  echo "Waiting for the images of $C4R_RELEASE from CI..."
  sleep 30
done

# Audit item 38: Caddy runs as caddy (uid 1000); volumes made while it ran as root are handed over.
# DAC_OVERRIDE: the folders already handed over are closed to everyone but caddy.
$compose run --rm --no-deps -T --user root --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --entrypoint chown web -R 1000:1000 /data /config
$compose up -d --no-build --wait
$compose ps
echo "$version" >> .deploy-history
# Images of versions no longer listed among the last ten are removed (audit item 48: disk).
keep="$(tail -n 10 .deploy-history | cut -c1-40 | sort -u)"
docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E '/c4rsys-(backend|web):[0-9a-f]{40}$' \
  | while read -r image; do
      echo "$keep" | grep -qx "${image##*:}" || docker rmi -f "$image" >/dev/null 2>&1 || true
    done
echo "Deployed $C4R_RELEASE"
