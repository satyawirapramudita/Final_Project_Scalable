#!/bin/sh
# Menyamakan password user MySQL dengan nilai di .env.
#
# `db/init.sql` membuat user dengan password contoh agar dev lokal mudah
# (ganti_auth_password, dst). Skrip ini dijalankan SESUDAH init.sql dan
# menjalankan ALTER USER memakai nilai dari .env, sehingga:
#   - .env adalah satu-satunya sumber kebenaran password,
#   - password contoh tidak pernah dipakai di produksi,
#   - tidak perlu menyunting file SQL yang di-commit.
#
# Syarat: password di .env memakai karakter URL-safe (A-Z a-z 0-9 - _) seperti
# yang dihasilkan `deploy/gen-secrets.sh`, agar tidak merusak sintaks SQL.
#
# Catatan: MySQL hanya menjalankan /docker-entrypoint-initdb.d pada VOLUME BARU.
# Untuk volume yang sudah ada, ubah password manual dengan ALTER USER
# (lihat docs/deploy-aws-tutorial.md bagian "Mengganti password volume lama").
set -eu

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
ALTER USER 'auth_user'@'%'      IDENTIFIED BY '${AUTH_DB_PASSWORD}';
ALTER USER 'poem_user'@'%'      IDENTIFIED BY '${POEM_DB_PASSWORD}';
ALTER USER 'wordcloud_user'@'%' IDENTIFIED BY '${WORDCLOUD_DB_PASSWORD}';
ALTER USER 'kbbi_user'@'%'      IDENTIFIED BY '${KBBI_DB_PASSWORD}';
ALTER USER 'search_user'@'%'    IDENTIFIED BY '${SEARCH_DB_PASSWORD}';
FLUSH PRIVILEGES;
SQL
