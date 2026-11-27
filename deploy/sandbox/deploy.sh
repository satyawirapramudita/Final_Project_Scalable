#!/usr/bin/env bash
# Deploy Poetify ke AWS Academy Sandbox: 3 server t3.small + S3 + CloudFront.
#
# Sandbox hanya mengizinkan EC2 t2/t3 sampai `small` (2 GiB) dan menghapus semua
# sumber daya saat timer sesi habis. Stack lengkap (±2,2 GiB) dipecah menjadi:
#
#   data   MySQL, Redis, RabbitMQ 3 node (satu klaster, port 5672/5673/5674)
#   app-1  gateway-a + 1 replika tiap layanan + Prometheus/Grafana
#   app-2  gateway-b + 1 replika tiap layanan
#
#   Browser ─HTTPS─▶ CloudFront ─┬─ /*      ─▶ S3 (frontend)
#                                └─ /api/*  ─▶ origin group: app-1, pindah ke app-2 bila 5xx/gagal
#
# Jalankan di AWS CloudShell (kredensial lab sudah tersedia):
#   git clone -b <branch> https://github.com/satyawirapramudita/Final_Project_Scalable
#   cd Final_Project_Scalable && bash deploy/sandbox/deploy.sh buat
#
# Perintah:
#   buat           semua langkah di bawah berurutan (±40-60 menit)
#   server         Security Group + 3 server (bootstrap otomatis)
#   tunggu         tunggu ketiga server selesai bootstrap
#   cdn            S3 frontend + CloudFront
#   seed           isi 1000 puisi contoh untuk pencarian (user 99)
#   uji            smoke test lewat CloudFront dan langsung ke app-1/app-2
#   info           alamat dan IP
#   jalankan <data|app-1|app-2> '<perintah>'   perintah di server lewat SSM
#   hapus          hapus semua yang dibuat skrip ini
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash di Windows: jangan ubah argumen "/..." menjadi path Windows

REGION="${AWS_REGION:-us-east-1}"
REPO="${REPO:-https://github.com/satyawirapramudita/Final_Project_Scalable}"
BRANCH="${BRANCH:-main}"
TYPE="${INSTANCE_TYPE:-t3.small}"
TAG="poetify-sandbox"
STATE="${HOME}/.poetify-sandbox"          # rahasia + ID sumber daya (per sesi)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$STATE"

aws_() { aws --region "$REGION" "$@"; }
langkah() { printf '\n==== %s ====\n' "$*"; }
simpan() { echo "$2" > "$STATE/$1"; }
baca() { cat "$STATE/$1" 2>/dev/null || true; }

# ── Rahasia: dibuat sekali per sesi, sama untuk ketiga server ──
rahasia() {
  [[ -f "$STATE/secrets.env" ]] && return
  python3 -c '
import secrets
for k in ["MYSQL_ROOT_PASSWORD","AUTH_DB_PASSWORD","POEM_DB_PASSWORD","WORDCLOUD_DB_PASSWORD",
          "KBBI_DB_PASSWORD","SEARCH_DB_PASSWORD","RABBITMQ_PASSWORD","RABBITMQ_ERLANG_COOKIE",
          "JWT_SECRET","GRAFANA_ADMIN_PASSWORD"]:
    print(f"{k}={secrets.token_hex(24)}")' | tr -d '\r' > "$STATE/secrets.env"
  chmod 600 "$STATE/secrets.env"
}

# Isi .env untuk satu server: .env.example + rahasia + setelan peran.
env_server() {  # env_server <data_ip>
  python3 -c '
import re, sys
vals = {}
for text in sys.argv[2:4]:
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
d = sys.argv[1]
vals.update({
    "MYSQL_HOST": d, "REDIS_HOST": d,
    "RABBITMQ_HOSTS": f"{d}:5672,{d}:5673,{d}:5674",
    "GATEWAY_PORT": "80", "ASSET_BASE_URL": "",
    # Satu replika per server aplikasi; dua server = dua replika per layanan.
    "AUTH_REPLICAS": "1", "POEM_REPLICAS": "1", "WORDCLOUD_REPLICAS": "1",
    "SEARCH_REPLICAS": "1", "KBBI_REPLICAS": "1", "NLP_WORKER_REPLICAS": "1",
    # CloudFront meneruskan dari sedikit IP edge: 20 r/s per IP terlalu ketat.
    "API_RATE_LIMIT": "500r/s", "API_BURST": "1000",
})
print("\n".join(f"{k}={v}" for k, v in vals.items()))
' "$1" "$(cat "$ROOT/.env.example")" "$(cat "$STATE/secrets.env")"
}

user_data() {  # user_data <role> <data_ip> [app2_ip]
  local envb64
  envb64="$(env_server "${2:-127.0.0.1}" | tr -d '\r' | base64 | tr -d '\n')"
  cat <<EOF
#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/poetify-bootstrap.log) 2>&1
dnf install -y git
git clone --depth 1 -b "$BRANCH" "$REPO" /opt/poetify
echo "$envb64" | base64 -d > /opt/poetify/.env
chmod 600 /opt/poetify/.env
ROLE="$1" DATA_IP="${2:-}" APP2_IP="${3:-}" bash /opt/poetify/deploy/sandbox/bootstrap.sh
EOF
}

luncurkan() {  # luncurkan <nama> <subnet> <user-data>  -> instance id
  aws_ ec2 run-instances --image-id "$(baca ami)" --instance-type "$TYPE" \
    --subnet-id "$2" --security-group-ids "$(baca sg)" --key-name vockey \
    --iam-instance-profile Name=LabInstanceProfile \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG-$1},{Key=Project,Value=$TAG}]" \
    --user-data "$3" --query 'Instances[0].InstanceId' --output text
}

ip_privat() { aws_ ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text; }
ip_publik() { aws_ ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text; }
dns_publik() { aws_ ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].PublicDnsName' --output text; }

server() {
  rahasia
  langkah "Jaringan"
  local vpc subnets sg
  vpc="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
  # Subnet bawaan di zona a dan b: app-1/data di a, app-2 di b (gateway di zona berbeda).
  mapfile -t subnets < <(aws_ ec2 describe-subnets --filters Name=vpc-id,Values="$vpc" Name=default-for-az,Values=true \
    --query 'sort_by(Subnets,&AvailabilityZone)[].SubnetId' --output text | tr '\t' '\n')
  sg="$(aws_ ec2 describe-security-groups --filters Name=group-name,Values="$TAG" Name=vpc-id,Values="$vpc" \
    --query 'SecurityGroups[0].GroupId' --output text)"
  if [[ "$sg" == "None" ]]; then
    sg="$(aws_ ec2 create-security-group --group-name "$TAG" --description "Poetify sandbox" --vpc-id "$vpc" \
      --query GroupId --output text)"
    # Antaranggota grup: semua port (MySQL, Redis, RabbitMQ, metrik) hanya di dalam VPC.
    aws_ ec2 authorize-security-group-ingress --group-id "$sg" \
      --ip-permissions "IpProtocol=-1,UserIdGroupPairs=[{GroupId=$sg}]" >/dev/null
    # Port 80: origin CloudFront dan akses langsung untuk uji beban dari laptop.
    aws_ ec2 authorize-security-group-ingress --group-id "$sg" --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
    # SSH (kunci vockey) untuk tunnel Grafana/Prometheus; SSM juga tersedia.
    aws_ ec2 authorize-security-group-ingress --group-id "$sg" --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null
  fi
  simpan sg "$sg"
  simpan ami "$(aws_ ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query Parameter.Value --output text)"
  echo "VPC $vpc, SG $sg, subnet ${subnets[*]}"

  langkah "Server data"
  local data app1 app2 dip a2ip
  data="$(luncurkan data "${subnets[0]}" "$(user_data data)")"; simpan data "$data"
  aws_ ec2 wait instance-running --instance-ids "$data"
  dip="$(ip_privat "$data")"; simpan data_ip "$dip"
  echo "data $data ($dip)"

  langkah "Server aplikasi"
  app2="$(luncurkan app-2 "${subnets[1]:-${subnets[0]}}" "$(user_data app "$dip")")"; simpan app2 "$app2"
  aws_ ec2 wait instance-running --instance-ids "$app2"
  a2ip="$(ip_privat "$app2")"
  app1="$(luncurkan app-1 "${subnets[0]}" "$(user_data app1 "$dip" "$a2ip")")"; simpan app1 "$app1"
  aws_ ec2 wait instance-running --instance-ids "$app1"
  echo "app-1 $app1 ($(ip_publik "$app1")), app-2 $app2 ($(ip_publik "$app2"))"
}

ssm() {  # ssm <instance-id> <perintah>  -> cetak keluaran
  local cid st params
  params="$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["3600"]}))' "$2")"
  cid="$(aws_ ssm send-command --instance-ids "$1" --document-name AWS-RunShellScript \
    --parameters "$params" --query Command.CommandId --output text)"
  while :; do
    sleep 3
    st="$(aws_ ssm get-command-invocation --command-id "$cid" --instance-id "$1" --query Status --output text 2>/dev/null || echo Pending)"
    [[ "$st" =~ ^(Success|Failed|Cancelled|TimedOut)$ ]] && break
  done
  aws_ ssm get-command-invocation --command-id "$cid" --instance-id "$1" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  [[ "$st" == "Success" ]]
}

id_peran() { case "$1" in data) baca data ;; app-1|app1) baca app1 ;; app-2|app2) baca app2 ;; *) echo "$1" ;; esac; }

tunggu() {
  langkah "Menunggu bootstrap (data ±5 menit, aplikasi ±15-25 menit: build image di 2 vCPU)"
  local t0=$SECONDS peran id ok
  for peran in data app-2 app-1; do
    id="$(id_peran "$peran")"
    until aws_ ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
        --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null | grep -q Online; do
      sleep 10
    done
    while :; do
      ok="$(ssm "$id" 'test -f /opt/poetify/.bootstrap-ok && echo SELESAI || (tail -1 /var/log/poetify-bootstrap.log 2>/dev/null | cut -c1-100)' 2>/dev/null | head -1 || true)"
      printf '\r[%4ds] %-6s %-100s' $((SECONDS - t0)) "$peran" "$ok"
      [[ "$ok" == SELESAI ]] && break
      if (( SECONDS - t0 > 3000 )); then echo; echo "Bootstrap $peran terlalu lama; lihat: bash $0 jalankan $peran 'tail -50 /var/log/poetify-bootstrap.log'"; return 1; fi
      sleep 20
    done
    echo
  done
  langkah "Container"
  for peran in data app-1 app-2; do
    echo "-- $peran"; ssm "$(id_peran "$peran")" "docker ps --format '{{.Names}} {{.Status}}' | sort"
  done
}

# ── S3 frontend + CloudFront ──
cdn() {
  local acct bucket web app1 app2 out
  acct="$(aws_ sts get-caller-identity --query Account --output text)"
  bucket="$(baca bucket)"
  if [[ -z "$bucket" ]]; then
    bucket="poetify-frontend-$acct-$(date +%s)"
    langkah "S3 $bucket"
    aws_ s3api create-bucket --bucket "$bucket" >/dev/null
    aws_ s3api put-public-access-block --bucket "$bucket" --public-access-block-configuration \
      BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false
    aws_ s3 website "s3://$bucket" --index-document index.html --error-document index.html
    aws_ s3api put-bucket-policy --bucket "$bucket" --policy \
      "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$bucket/*\"}]}"
    simpan bucket "$bucket"
  fi
  aws_ s3 sync "$ROOT/frontend" "s3://$bucket" --delete --only-show-errors
  web="$bucket.s3-website-$REGION.amazonaws.com"
  app1="$(dns_publik "$(baca app1)")"; app2="$(dns_publik "$(baca app2)")"

  [[ -n "$(baca cf_id)" ]] && { echo "CloudFront sudah ada: $(baca cf_dns)"; return; }
  langkah "CloudFront (origin group app-1 -> app-2)"
  local cfg id
  for mode in group split; do
    cfg="$(python3 - "$web" "$app1" "$app2" "$mode" <<'PY'
import json, sys, time
web, app1, app2, mode = sys.argv[1:5]
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
def origin(oid, dom, attempts=3, timeout=10):
    return {"Id": oid, "DomainName": dom, "OriginPath": "", "CustomHeaders": {"Quantity": 0},
            "ConnectionAttempts": attempts, "ConnectionTimeout": timeout,
            "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "http-only",
                                   "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                                   "OriginReadTimeout": 30, "OriginKeepaliveTimeout": 5}}
def methods(all_):
    m = ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"] if all_ else ["GET", "HEAD", "OPTIONS"]
    return {"Quantity": len(m), "Items": m, "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}}
def behavior(path, target, all_methods):
    b = {"TargetOriginId": target, "ViewerProtocolPolicy": "redirect-to-https", "Compress": True,
         "AllowedMethods": methods(all_methods), "CachePolicyId": CACHING_DISABLED,
         "OriginRequestPolicyId": ALL_VIEWER_EXCEPT_HOST}
    if path:
        b["PathPattern"] = path
    return b
# app-1: 1 percobaan, timeout 3 dtk -> CloudFront cepat pindah ke app-2 saat app-1 mati.
origins = [origin("s3-frontend", web), origin("app-1", app1, 1, 3), origin("app-2", app2)]
group = {"Id": "api-group", "FailoverCriteria": {"StatusCodes": {"Quantity": 4, "Items": [500, 502, 503, 504]}},
         "Members": {"Quantity": 2, "Items": [{"OriginId": "app-1"}, {"OriginId": "app-2"}]}}
if mode == "group":
    behaviors = [behavior("/api/*", "api-group", True)]
else:
    # Bila CloudFront menolak origin group pada behavior yang mengizinkan POST:
    # jalur baca murni lewat origin group, sisanya (tulis) ke app-1.
    behaviors = [behavior(p, "api-group", False) for p in ("/api/search*", "/api/wordcloud/*", "/api/kbbi/*")]
    behaviors.append(behavior("/api/*", "app-1", True))
default = {"TargetOriginId": "s3-frontend", "ViewerProtocolPolicy": "redirect-to-https", "Compress": True,
           "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                              "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
           "CachePolicyId": CACHING_OPTIMIZED}
print(json.dumps({
    "CallerReference": f"poetify-{time.time()}", "Comment": "Poetify sandbox", "Enabled": True,
    "DefaultRootObject": "index.html", "PriceClass": "PriceClass_100",
    "Origins": {"Quantity": len(origins), "Items": origins},
    "OriginGroups": {"Quantity": 1, "Items": [group]},
    "DefaultCacheBehavior": default,
    "CacheBehaviors": {"Quantity": len(behaviors), "Items": behaviors},
}))
PY
)"
    if out="$(aws cloudfront create-distribution --distribution-config "$cfg" \
        --query 'Distribution.[Id,DomainName]' --output text 2>&1)"; then
      read -r id dns <<<"$out"
      simpan cf_id "$id"; simpan cf_dns "$dns"; simpan cf_mode "$mode"
      echo "distribusi $id ($dns), mode $mode"
      break
    fi
    echo "mode $mode ditolak: $out"
    [[ "$mode" == split ]] && return 1
  done
  echo "Menunggu CloudFront Deployed (5-15 menit)..."
  aws cloudfront wait distribution-deployed --id "$(baca cf_id)"
  echo "CloudFront siap: https://$(baca cf_dns)/"
}

seed() {
  langkah "Seed 1000 puisi contoh (user 99) untuk pencarian"
  ssm "$(baca app1)" 'cd /opt/poetify && docker compose exec -T search python - --count 1000 --user 99 < scripts/publish_fake_events.py | tail -3'
}

uji() {
  local cf a1 a2
  cf="$(baca cf_dns)"; a1="$(ip_publik "$(baca app1)")"; a2="$(ip_publik "$(baca app2)")"
  langkah "Smoke test"
  printf '%-32s %s\n' "app-1 /health"   "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://$a1/health")"
  printf '%-32s %s\n' "app-2 /health"   "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://$a2/health")"
  if [[ -n "$cf" ]]; then
    printf '%-32s %s\n' "CloudFront / (S3)"         "$(curl -s -o /dev/null -m 15 -w '%{http_code}' "https://$cf/")"
    printf '%-32s %s\n' "CloudFront /api/search"    "$(curl -s -o /dev/null -m 15 -w '%{http_code}' "https://$cf/api/search?q=a")"
    printf '%-32s %s\n' "CloudFront POST /api/auth" "$(curl -s -o /dev/null -m 15 -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"username":"x","password":"y"}' "https://$cf/api/auth/login")"
  fi
  echo "(200 = halaman/health; 401 = request sampai ke layanan, ditolak karena tanpa token/login salah)"
}

info() {
  echo "CloudFront : https://$(baca cf_dns)/  (mode $(baca cf_mode))"
  for p in app-1 app-2 data; do
    local id; id="$(id_peran "$p")"
    [[ -n "$id" ]] && printf '%-6s %s  publik %s  privat %s\n' "$p" "$id" "$(ip_publik "$id")" "$(ip_privat "$id")"
  done
  echo "Grafana    : ssh -i labsuser.pem -L 3000:localhost:3000 -L 9090:localhost:9090 ec2-user@<IP publik app-1>"
  echo "             lalu http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD di $STATE/secrets.env)"
}

hapus() {
  langkah "Menghapus sumber daya $TAG"
  local id
  id="$(baca cf_id)"
  if [[ -n "$id" ]]; then
    local etag cfg
    etag="$(aws cloudfront get-distribution-config --id "$id" --query ETag --output text)"
    cfg="$(aws cloudfront get-distribution-config --id "$id" --query DistributionConfig --output json \
      | python3 -c 'import json,sys; c=json.load(sys.stdin); c["Enabled"]=False; print(json.dumps(c))')"
    aws cloudfront update-distribution --id "$id" --if-match "$etag" --distribution-config "$cfg" >/dev/null
    echo "CloudFront dinonaktifkan; menunggu (5-15 menit) sebelum dihapus..."
    aws cloudfront wait distribution-deployed --id "$id"
    aws cloudfront delete-distribution --id "$id" \
      --if-match "$(aws cloudfront get-distribution-config --id "$id" --query ETag --output text)"
  fi
  local ids
  ids="$(aws_ ec2 describe-instances --filters "Name=tag:Project,Values=$TAG" \
    Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086
    aws_ ec2 terminate-instances --instance-ids $ids >/dev/null
    # shellcheck disable=SC2086
    aws_ ec2 wait instance-terminated --instance-ids $ids
  fi
  [[ -n "$(baca bucket)" ]] && aws_ s3 rb "s3://$(baca bucket)" --force >/dev/null
  [[ -n "$(baca sg)" ]] && aws_ ec2 delete-security-group --group-id "$(baca sg)" 2>/dev/null || true
  rm -rf "$STATE"
  echo "selesai"
}

case "${1:-}" in
  buat)     server; tunggu; cdn; seed; uji; info ;;
  server)   server ;;
  tunggu)   tunggu ;;
  cdn)      cdn ;;
  seed)     seed ;;
  uji)      uji ;;
  info)     info ;;
  jalankan) shift; ssm "$(id_peran "$1")" "$2" ;;
  hapus)    hapus ;;
  *)        sed -n '2,30p' "$0"; exit 1 ;;
esac
