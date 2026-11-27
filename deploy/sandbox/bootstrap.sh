#!/usr/bin/env bash
# Bootstrap satu server sandbox (Amazon Linux 2023). Dipanggil user-data yang dibuat
# deploy/sandbox/deploy.sh setelah repo di-clone ke /opt/poetify dan .env ditulis.
#
#   ROLE=data                      MySQL, Redis, RabbitMQ 3 node
#   ROLE=app  DATA_IP=...          gateway + 1 replika tiap layanan (app-2, "gateway-b")
#   ROLE=app1 DATA_IP=... APP2_IP=...   sama dengan app + Prometheus/Grafana (app-1, "gateway-a")
#
# Log: /var/log/poetify-bootstrap.log. Selesai bila /opt/poetify/.bootstrap-ok ada.
set -euxo pipefail
cd /opt/poetify

COMPOSE_VERSION=v2.29.7
BUILDX_VERSION=v0.17.1

# ── Swap 2 GiB: penyangga lonjakan memori di server 2 GiB (bukan pengganti RAM) ──
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10
fi

# ── Docker + Compose v2 + buildx ──
dnf install -y docker
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
curl -fsSL "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64" \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod +x /usr/local/lib/docker/cli-plugins/*
# Rotasi log Docker (container stack juga memakai max-size 10m dari compose).
docker version

dc() { docker compose -f docker-compose.yml "$@"; }

tunggu_port() {  # tunggu_port <host> <port> <batas-detik>
  local t0=$SECONDS
  until (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null; do
    (( SECONDS - t0 > $3 )) && { echo "timeout menunggu $1:$2"; return 1; }
    sleep 5
  done
}

case "$ROLE" in
  data)
    dc -f deploy/sandbox/data.yml pull mysql redis rabbit-1
    dc -f deploy/sandbox/data.yml up -d --no-deps mysql redis rabbit-1 rabbit-2 rabbit-3
    ;;
  app|app1)
    SVCS="gateway auth poem wordcloud search kbbi-linker nlp-worker autoheal nginx-exporter"
    if [[ "$ROLE" == "app1" ]]; then
      SVCS="$SVCS prometheus grafana"
      sed -e "s/__APP2__/${APP2_IP}/g" -e "s/__DATA__/${DATA_IP}/g" \
        deploy/sandbox/prometheus.yml.tpl > deploy/sandbox/prometheus.yml
    fi
    # Build sambil server data bersiap (build ±10-15 menit di 2 vCPU).
    dc -f deploy/sandbox/app.yml build
    tunggu_port "$DATA_IP" 3306 1200
    tunggu_port "$DATA_IP" 5672 1200
    tunggu_port "$DATA_IP" 5673 600
    tunggu_port "$DATA_IP" 5674 600
    tunggu_port "$DATA_IP" 6379 600
    # shellcheck disable=SC2086
    dc -f deploy/sandbox/app.yml up -d --no-deps $SVCS
    ;;
  *)
    echo "ROLE tidak dikenal: $ROLE"; exit 1 ;;
esac

touch /opt/poetify/.bootstrap-ok
docker ps --format '{{.Names}} {{.Status}}'
