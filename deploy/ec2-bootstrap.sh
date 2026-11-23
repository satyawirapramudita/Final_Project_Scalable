#!/usr/bin/env bash
# User-data EC2 (Ubuntu 24.04): Docker + Compose v2, swap 4 GB, rotasi log.
#
# Cara pakai: saat launch instance, buka "Advanced details" -> "User data",
# tempel isi file ini (mode text), lalu launch. Progres bisa dipantau lewat
#   ssh ubuntu@<ip> 'tail -f /var/log/cloud-init-output.log'
#
# Skrip ini idempoten: aman dijalankan ulang.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    docker.io docker-compose-v2 git jq unzip ca-certificates

systemctl enable --now docker
usermod -aG docker ubuntu || true

# Swap: 19 container + MySQL pada instance 4-8 GB butuh cadangan memori.
if [[ ! -f /swapfile ]]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-puisi-swap.conf
    sysctl -p /etc/sysctl.d/99-puisi-swap.conf
fi

# Rotasi log di level daemon (lapisan kedua setelah opsi logging di compose).
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
systemctl restart docker

echo "bootstrap selesai: $(date -Is)" | tee /var/log/puisi-bootstrap.done
