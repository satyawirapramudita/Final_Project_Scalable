#!/usr/bin/env bash
# Backup harian MySQL (semua database layanan) + unggah ke S3 (opsional).
#
#   bash deploy/backup-mysql.sh
#   BACKUP_S3=s3://bucket/backups bash deploy/backup-mysql.sh
#
# Cron contoh (jalankan sebagai user yang ada di grup docker):
#   15 2 * * * cd /home/ubuntu/Final_Project_Scalable && \
#     BACKUP_S3=s3://puisi-backups/db bash deploy/backup-mysql.sh >> /var/log/puisi-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env}"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: $ENV_FILE tidak ditemukan" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

BACKUP_DIR="${BACKUP_DIR:-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/mysql-$STAMP.sql.gz"
mkdir -p "$BACKUP_DIR"

COMPOSE=(docker compose -f docker-compose.yml)
[[ -f docker-compose.prod.yml ]] && COMPOSE+=(-f docker-compose.prod.yml)

"${COMPOSE[@]}" exec -T mysql \
    mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
    --single-transaction --quick --routines --events \
    --databases auth_db poem_db wordcloud_db kbbi_db search_db \
    | gzip -9 > "$OUT"

echo "backup: $OUT ($(du -h "$OUT" | cut -f1))"

if [[ -n "${BACKUP_S3:-}" ]]; then
    aws s3 cp "$OUT" "${BACKUP_S3%/}/$(basename "$OUT")"
    echo "diunggah ke ${BACKUP_S3%/}/$(basename "$OUT")"
fi

find "$BACKUP_DIR" -name 'mysql-*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
