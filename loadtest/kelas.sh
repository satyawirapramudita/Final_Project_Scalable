#!/usr/bin/env bash
# Stress test kelas (7,5 menit beban, <10 menit total) terhadap deploy AWS Academy Sandbox.
#
#   bash loadtest/kelas.sh
#
# Jalankan dari mesin yang dipakai untuk deploy (kredensial lab + ~/.poetify-sandbox),
# dengan k6 terpasang (atau K6=/path/k6). Fase: baseline, ramp 20..320 VU, spike 300 VU,
# pulih, lalu poem di app-1 dimatikan 30 detik. Beban: 50% search, 20% daftar puisi,
# 10% wordcloud, 10% kbbi, 10% submit. Hasil disimpan di loadtest/results/kelas-<waktu>/.
set -euo pipefail
export MSYS_NO_PATHCONV=1
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")/.."

DEPLOY="${DEPLOY:-bash deploy/sandbox/deploy.sh}"
K6="${K6:-k6}"
PY="${PY:-$(python3 -c "" >/dev/null 2>&1 && echo python3 || echo python)}"
SCALE="${SCALE:-1}"
command -v "$K6" >/dev/null || { echo "k6 tidak ditemukan. Pasang k6 atau set K6=/path/k6"; exit 1; }

OUT="loadtest/results/kelas-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
langkah() { echo; echo "==== $(date +%H:%M:%S)  $* ===="; }
jalan() { $DEPLOY jalankan "$1" "$2"; }
skala() { "$PY" -c "print(max(1, round($1 * $SCALE)))" | tr -d '\r'; }
C='cd /opt/poetify && docker compose -f docker-compose.yml -f deploy/sandbox/app.yml'
SQL='R=$(grep ^MYSQL_ROOT_PASSWORD= /opt/poetify/.env | cut -d= -f2-); docker exec puisi-mysql mysql -uroot -p"$R" -hlocalhost -N -e'

# Mencetak "total done outbox antrean dlq" untuk puisi yang dibuat uji beban.
hitung() {
  jalan data "$SQL \"SELECT COUNT(*), COALESCE(SUM(status='done'),0), (SELECT COUNT(*) FROM poem_db.outbox WHERE published_at IS NULL) FROM poem_db.puisi WHERE judul LIKE 'Stress AWS %'\" 2>/dev/null
docker exec puisi-rabbit-1 rabbitmqctl -q list_queues name messages | awk '\$2 ~ /^[0-9]+\$/ { if (\$1 ~ /dlq\$/) d += \$2; else q += \$2 } END { print q+0, d+0 }'" \
    | grep -oE '[0-9]+' | tr '\n' ' '
}

langkah "Persiapan"
IP="${APP1_IP:-}"
[[ -n "$IP" || -n "${BASE_URL:-}" ]] || IP="$($DEPLOY info | awk '$1=="app-1" {print $4}')"
BASE="${BASE_URL:-http://$IP}"
curl -fsS -m 20 "$BASE/health" >/dev/null || { echo "$BASE/health tidak menjawab"; exit 1; }
echo "target: $BASE"

# 50 pengguna uji baru tiap kali dijalankan (agar daftar puisi mulai dari kosong): daftar puisi mengembalikan SEMUA puisi milik pengguna, jadi satu token
# pemilik 1000 puisi contoh akan mendominasi hasil.
AWAL=$(( 10000 + $(date +%s) % 800000 ))
TOKENS="$(jalan app-1 "$C exec -T poem python -c \"from shared.security import create_token as t; print(','.join(t(user_id=i, username=f'beban{i}', nama='Beban', expires_minutes=120) for i in range($AWAL, $AWAL + 50)))\"" \
  | grep -oE 'eyJ[A-Za-z0-9_.,-]+' | head -1)"
[[ -n "$TOKENS" ]] || { echo "gagal membuat token"; exit 1; }
IDS="$(curl -fsS -m 30 -H "Authorization: Bearer ${TOKENS%%,*}" "$BASE/api/search?q=senja&limit=50" \
  | "$PY" -c 'import json,sys; print(",".join(str(i["id"]) for i in json.load(sys.stdin)["items"]))' | tr -d '\r')"
[[ -n "$IDS" ]] || { echo "tidak ada puisi contoh; jalankan: $DEPLOY seed"; exit 1; }

pulihkan() {
  # Penting: matikan pengatur waktu injeksi, agar poem tidak dimatikan setelah skrip berhenti.
  if [[ -n "${INJEKSI:-}" ]]; then kill "$INJEKSI" 2>/dev/null || true; fi
  langkah "Memulihkan poem dan rate limit"
  jalan app-1 "$C up -d --no-deps poem >/dev/null 2>&1
cd /opt/poetify; if [ -f .env.sebelum-stress ]; then mv .env.sebelum-stress .env; fi
docker compose -f docker-compose.yml -f deploy/sandbox/app.yml up -d --no-deps --force-recreate gateway >/dev/null 2>&1 && echo dipulihkan" \
    || echo "GAGAL memulihkan: periksa poem dan gateway di app-1"
}
INJEKSI=""
trap pulihkan EXIT
trap "exit 130" INT TERM

echo "menaikkan rate limit gateway app-1 selama uji (dikembalikan di akhir)"
jalan app-1 "cd /opt/poetify && cp -n .env .env.sebelum-stress; sed -i '/^API_RATE_LIMIT=/d;/^API_BURST=/d' .env && printf 'API_RATE_LIMIT=100000r/s\nAPI_BURST=100000\n' >> .env && docker compose -f docker-compose.yml -f deploy/sandbox/app.yml up -d --no-deps --force-recreate gateway >/dev/null 2>&1" >/dev/null
for _ in $(seq 30); do curl -fsS -m 5 "$BASE/health" >/dev/null 2>&1 && break; sleep 2; done

read -r SEBELUM _ <<< "$(hitung)"
[[ -n "$SEBELUM" ]] || { echo "tidak bisa membaca database di server data"; exit 1; }

langkah "Beban dimulai (±$(skala 450) detik)"
(
  sleep "$(skala 380)"
  langkah "INJEKSI: stop poem di app-1"
  jalan app-1 "$C stop poem >/dev/null 2>&1; echo poem mati"
  sleep "$(skala 30)"
  langkah "PULIHKAN: nyalakan poem"
  jalan app-1 "$C up -d --no-deps poem >/dev/null 2>&1; echo poem dinyalakan"
) &
INJEKSI=$!
"$K6" run --log-output=file="$OUT/k6.log" -e PROFILE=kelas -e BASE_URL="$BASE" -e TOKENS="$TOKENS" -e IDS="$IDS" -e SCALE="$SCALE" \
  --out csv="$OUT/k6.csv" loadtest/aws_stress.js 2>&1 | tee "$OUT/k6.txt"
wait "$INJEKSI"

langkah "Menunggu antrean terkuras (maks 5 menit)"
mulai=$(date +%s)
while :; do
  read -r TOTAL DONE OUTBOX ANTRE DLQ <<< "$(hitung)"
  lama=$(( $(date +%s) - mulai ))
  if [[ -z "$DLQ" ]]; then echo "gagal membaca database/antrean, coba lagi"; (( lama > 300 )) && break; sleep 5; continue; fi
  echo "+${lama}s puisi uji $((TOTAL - SEBELUM)), belum done $((TOTAL - DONE)), outbox $OUTBOX, antrean $ANTRE, DLQ $DLQ"
  if (( TOTAL == DONE && OUTBOX == 0 && ANTRE == 0 )); then break; fi
  if (( lama > 300 )); then break; fi
  sleep 5
done

langkah "Hasil"
{
  "$PY" loadtest/laporan_stress.py "$OUT/k6.csv"
  echo
  echo "Integritas: $((TOTAL - SEBELUM)) puisi tersimpan, belum dianalisis $((TOTAL - DONE)), outbox tertunda $OUTBOX, pesan antrean $ANTRE, DLQ $DLQ."
  echo "Antrean terkuras ±${lama} detik setelah beban berhenti."
  if [[ -z "$DLQ" ]]; then
    echo "PERHATIAN: database/antrean tidak terbaca, integritas belum diperiksa."
  elif (( TOTAL == SEBELUM )); then
    echo "PERHATIAN: tidak ada puisi uji yang tersimpan."
  elif (( TOTAL == DONE && OUTBOX == 0 && ANTRE == 0 && DLQ == 0 )); then
    echo "LULUS: tidak ada data yang tertinggal."
  else
    echo "PERHATIAN: ada yang tertinggal (lihat angka di atas)."
  fi
} | tee "$OUT/ringkasan.txt"
echo "disimpan di $OUT"
