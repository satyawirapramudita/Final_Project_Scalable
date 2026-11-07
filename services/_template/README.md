# Template Layanan

Salin folder ini untuk membuat layanan baru, lalu:

1. Ganti nama folder → `services/<nama>`.
2. Sesuaikan `SERVICE_NAME` (env di `docker-compose.yml`) dan `EXPOSE`/port di `Dockerfile`.
3. Tambahkan service di `docker-compose.yml` + route di `gateway/conf.d/default.conf`.
4. Setiap layanan wajib punya `/health` dan `/metrics`.
