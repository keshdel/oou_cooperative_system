#!/usr/bin/env bash
# One-time bootstrap for a fresh Ubuntu VPS: installs Docker + Compose and opens
# the firewall for web traffic. Run as root (or with sudo).
set -euo pipefail

echo "==> Installing Docker Engine + Compose plugin"
apt-get update
apt-get install -y ca-certificates curl git ufw
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "==> Opening firewall for SSH + web (80/443)"
ufw allow OpenSSH || true
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable || true

echo "==> Done. Docker version:"
docker --version
docker compose version
echo
echo "Next:"
echo "  git clone https://github.com/keshdel/oou_cooperative_system.git"
echo "  cd oou_cooperative_system/deploy/vps"
echo "  ./add-client.sh client1 client1.yourdomain.com"
