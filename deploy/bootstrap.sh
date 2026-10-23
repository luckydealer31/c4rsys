#!/usr/bin/env bash
# First setup of a clean Ubuntu 24.04 server for the stage 1 test stand (T20). Run once as root:
#   bash bootstrap.sh
# Installs Docker from the official repository, allows only SSH, HTTP and HTTPS in the firewall,
# turns off SSH password login when a key is already installed, clones the code and creates
# .env.prod with generated secrets. The secrets are never printed. Safe to run again.
set -euo pipefail

REPO="${C4R_REPO:-https://github.com/luckydealer31/c4rsys.git}"
DIR="${C4R_DIR:-/opt/c4rsys}"
SITE="${C4R_SITE:-test.c4rsys.com}"
export DEBIAN_FRONTEND=noninteractive

[ "$(id -u)" -eq 0 ] || { echo "Run as root"; exit 1; }
. /etc/os-release
[ "$ID" = "ubuntu" ] || { echo "Ubuntu is expected, found $ID"; exit 1; }

echo "== system packages"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl git ufw openssl

echo "== docker"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker compose version

echo "== firewall: only SSH, HTTP, HTTPS"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "== SSH: keys only"
if [ -s /root/.ssh/authorized_keys ]; then
  # sshd takes the first value it reads; provider images ship 50-cloud-init.conf with
  # «PasswordAuthentication yes», so this file must sort before it.
  rm -f /etc/ssh/sshd_config.d/60-c4rsys-keys-only.conf
  cat > /etc/ssh/sshd_config.d/01-c4rsys-keys-only.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t && systemctl reload ssh
  [ "$(sshd -T | awk '/^passwordauthentication /{print $2}')" = "no" ] \
    || { echo "Password login is still on"; exit 1; }
else
  echo "No key in /root/.ssh/authorized_keys: password login is left as it is"
fi

echo "== code"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi

echo "== settings"
ENV="$DIR/.env.prod"
if [ ! -f "$ENV" ]; then
  umask 077
  sed \
    -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(openssl rand -hex 32)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" \
    -e "s|^C4R_SITE_ADDRESS=.*|C4R_SITE_ADDRESS=${SITE}|" \
    -e "s|^C4R_PUBLIC_URL=.*|C4R_PUBLIC_URL=https://${SITE}|" \
    -e "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=${SITE},127.0.0.1|" \
    "$DIR/deploy/env.prod.example" > "$ENV"
  echo "Created $ENV with generated secrets"
else
  echo "$ENV exists, left as it is"
fi
chmod 600 "$ENV"

cat <<EOF

Done. Next:
  1. Put the AWS keys of the SES user into $ENV (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY):
       nano $ENV
  2. Point the DNS record A ${SITE} to this server.
  3. Start: cd $DIR && ./scripts/deploy.sh
EOF
