# Ketahanan submit: idempotency key, jitter retry, connection pool

- [x] Perbaiki 3 test auth yang mengharapkan 422 (kontrak monolit sekarang 400)
- [ ] Tindak lanjut infra: init.sql masih hardcode password dev — ganti ke inisialisasi
      berbasis env (mis. init.sh yang membaca *_DB_PASSWORD dari .env) agar .env
      bebas memakai secret acak
- [x] `shared/backoff.py`: full jitter (backoff eksponensial) + equal jitter (delay tetap)
- [x] Terapkan jitter di `shared/rabbit.py` dan loop `init_schema` 5 service
- [x] Connection pool DBUtils untuk poem & auth (env `DB_POOL_MIN`/`DB_POOL_MAX`)
- [x] `shared/idempotency.py` + integrasi POST/PUT/DELETE poem (Redis TTL 24 jam, 409 konflik)
- [x] Frontend: retry + backoff + jitter di `app.js` (GET selalu, submit pakai idempotency key)
- [x] Test unit, loadtest submit, dokumentasi, panel Grafana
- [x] Perbaikan infra startup: SELinux bind-mount (`z`), autoheal tag cacat +
      akses docker.sock, GRAFANA_PORT, sinkronisasi password init.sql vs .env
- [x] Pentest targeted 17/17 PASS (scripts/pentest_targeted.py)
- [ ] Stress test k6 (submit-normal, submit-duplikat, key-bentrok, search baseline)

# Penyelesaian kbbi-linker

- [x] Validasi event dan tes regresi
- [x] Importer idempoten tanpa definisi basi
- [x] Log JSON terkorlasi
- [x] Tes, compile, dan Compose config lulus
- [ ] Build serta integrasi Docker terverifikasi — terblokir: Docker daemon tidak tersedia
- [x] Dokumentasi dan review final selesai
