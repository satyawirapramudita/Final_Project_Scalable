#!/usr/bin/env bash
# Membuat .env produksi dari .env.example dengan secret acak yang kuat.
#
#   bash deploy/gen-secrets.sh            # buat .env baru
#   bash deploy/gen-secrets.sh --force    # timpa .env yang ada
#
# Password user MySQL TIDAK perlu disamakan manual dengan db/init.sql:
# db/02-env-passwords.sh menjalankan ALTER USER memakai nilai dari .env saat
# volume MySQL pertama kali dibuat.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env && "${1:-}" != "--force" ]]; then
    echo "ERROR: .env sudah ada. Jalankan 'bash deploy/gen-secrets.sh --force' untuk menimpa." >&2
    exit 1
fi

python3 - <<'PY'
import re
import secrets
from pathlib import Path

src = Path(".env.example")
dst = Path(".env")
spec = {
    "MYSQL_ROOT_PASSWORD": 24,
    "AUTH_DB_PASSWORD": 24,
    "POEM_DB_PASSWORD": 24,
    "WORDCLOUD_DB_PASSWORD": 24,
    "KBBI_DB_PASSWORD": 24,
    "SEARCH_DB_PASSWORD": 24,
    "RABBITMQ_PASSWORD": 24,
    "JWT_SECRET": 48,
    "GRAFANA_ADMIN_PASSWORD": 20,
}
text = src.read_text(encoding="utf-8")
for key, length in spec.items():
    pattern = re.compile(rf"^{key}=.*$", re.M)
    if not pattern.search(text):
        raise SystemExit(f"kunci {key} tidak ditemukan di .env.example")
    text = pattern.sub(f"{key}={secrets.token_urlsafe(length)[:length]}", text)
dst.write_text(text, encoding="utf-8")
dst.chmod(0o600)
print("Membuat .env dengan secret acak:")
for key in spec:
    print(f"  - {key}")
PY

cat <<'EOF'

Selesai. Langkah berikutnya:
  1. Isi ASSET_BASE_URL setelah distribusi CloudFront dibuat.
  2. Sesuaikan bila perlu: SEARCH_WORKERS, *_REPLICAS, API_RATE_LIMIT.
  3. JANGAN commit .env (sudah masuk .gitignore).
EOF
