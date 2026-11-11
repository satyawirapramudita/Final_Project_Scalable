# Deployment `kbbi-linker` pada Beberapa EC2

Paket ini memisahkan MySQL, Redis, RabbitMQ, dan `kbbi-linker` agar masing-masing dapat dijalankan pada EC2 sendiri. Komunikasi antar-instance menggunakan private DNS atau private IP AWS, bukan nama service Docker.

## File

| File | EC2 tujuan | Port privat |
|---|---|---:|
| `compose.mysql.yml` | MySQL | 3306 |
| `compose.redis.yml` | Redis | 6379 |
| `compose.rabbitmq.yml` | RabbitMQ | 5672 |
| `compose.service.yml` | kbbi-linker | 8005 |

Port tersebut harus dibatasi menggunakan Security Group. Jangan membuka MySQL, Redis, atau RabbitMQ ke `0.0.0.0/0`.

## Persiapan

Pada setiap EC2, salin contoh environment:

```bash
cp deploy/kbbi/.env.example deploy/kbbi/.env
chmod 600 deploy/kbbi/.env
```

Hapus variabel yang tidak diperlukan pada instance tersebut dan ganti seluruh secret. Nilai berikut harus konsisten:

- `KBBI_DB_PASSWORD` pada MySQL dan service.
- `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, dan `RABBITMQ_VHOST` pada RabbitMQ dan service.
- `JWT_SECRET` harus sama dengan auth service.

## Urutan Deployment

### 1. MySQL EC2

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.mysql.yml up -d
```

Official MySQL image membuat `kbbi_db` dan `kbbi_user`. Tabel dibuat oleh `kbbi-linker` ketika service pertama kali terhubung.

### 2. Redis EC2

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.redis.yml up -d
```

Redis bersifat cache fail-open. Port 6379 harus hanya menerima koneksi dari Security Group service.

### 3. RabbitMQ EC2

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.rabbitmq.yml up -d
```

Management UI secara default hanya bind ke `127.0.0.1`. Gunakan SSH tunnel bila perlu:

```bash
ssh -L 15672:127.0.0.1:15672 ubuntu@RABBITMQ_PUBLIC_OR_BASTION_IP
```

### 4. kbbi-linker EC2

Pastikan `KBBI_DB_HOST`, `KBBI_REDIS_HOST`, dan `KBBI_RABBITMQ_HOST` berisi private DNS/IP yang benar, lalu jalankan:

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.service.yml up -d --build
```

Periksa status dan log:

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.service.yml ps
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.service.yml logs --tail=100 kbbi-linker
```

## Impor Dataset

Dataset harus memiliki izin penggunaan dan tidak boleh dimasukkan ke Git. Jalankan pada EC2 service:

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.service.yml run --rm \
  -v /home/ubuntu/kbbi.csv:/data/kbbi.csv:ro \
  kbbi-linker python scripts/import_kbbi_csv.py \
  /data/kbbi.csv --version kbbi-v-local
```

Nilai `--version` harus sama dengan `KBBI_DATASET_VERSION`.

## Security Group Minimum

- MySQL EC2: port 3306 hanya dari Security Group `kbbi-linker`.
- Redis EC2: port 6379 hanya dari Security Group `kbbi-linker`.
- RabbitMQ EC2: port 5672 hanya dari Security Group service producer/consumer.
- Service EC2: port 8005 hanya dari Security Group gateway atau load balancer.
- SSH port 22 hanya dari bastion, VPN, atau IP administrator.

## Menghentikan Komponen

Jalankan pada EC2 yang sesuai:

```bash
docker compose --env-file deploy/kbbi/.env \
  -f deploy/kbbi/compose.<komponen>.yml down
```

Jangan tambahkan `-v` kecuali volume data memang hendak dihapus permanen.
