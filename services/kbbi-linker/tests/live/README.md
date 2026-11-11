# Pengujian integrasi `kbbi-linker`

Suite ini memakai MySQL, Redis, dan RabbitMQ sungguhan. Port pengujian sengaja
dibedakan dari port standar: MySQL `13306`, Redis `16379`, RabbitMQ `15673`.

```powershell
docker compose -f services/kbbi-linker/tests/live/compose.yml up -d
$env:KBBI_LIVE = "1"
python -m pytest services/kbbi-linker/tests -q
```

Tanpa `KBBI_LIVE=1`, pengujian integrasi dilewati dan unit test tetap dapat
dijalankan tanpa Docker.

Setelah selesai:

```powershell
docker compose -f services/kbbi-linker/tests/live/compose.yml down
Remove-Item Env:KBBI_LIVE
```
