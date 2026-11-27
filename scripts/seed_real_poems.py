"""Seed puisi contoh lewat jalur submit asli (POST /api/poems di layanan poem).

Berbeda dengan publish_fake_events.py (hanya mengisi indeks search), puisi ini
tersimpan di poem_db dan dianalisis nlp-worker, wordcloud, dan kbbi-linker,
sehingga setiap hasil pencarian bisa dibuka.

Dijalankan di dalam container poem:

    docker compose exec -T poem python - --count 1000 --user 99 < scripts/seed_real_poems.py

Aman diulang dalam masa berlaku Idempotency-Key: puisi ke-i memakai key yang sama.
Skrip selesai setelah semua puisi berstatus done dan antrean kosong.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from shared import events
from shared.rabbit import connect
from shared.security import create_token

API = "http://localhost:8002/api/poems"
KATA = (
    "senja laut rindu hujan malam bintang angin embun bulan sunyi kenangan pagi "
    "cakrawala ombak pantai gunung sungai daun kabut cahaya bayang mimpi harapan "
    "luka cinta waktu jalan langit awan api abu doa sepi fajar kunang rumput badai"
).split()
KATEGORI = ("Alam", "Cinta", "Sedih", "Rindu", "Harapan")
PENGHUBUNG = ("di", "dan", "yang", "pada", "tanpa", "bersama", "menuju")
ANTREAN = (events.QUEUE_NLP, events.QUEUE_POEM, events.QUEUE_WORDCLOUD,
           events.QUEUE_KBBI, events.QUEUE_SEARCH)


def buat_puisi(i: int) -> dict:
    rng = random.Random(i)  # isi sama setiap kali dijalankan
    judul = " ".join(w.capitalize() for w in rng.sample(KATA, 2))
    baris = []
    for _ in range(3):
        kata = [rng.choice(KATA) if rng.random() < 0.6 else rng.choice(PENGHUBUNG)
                for _ in range(rng.randint(5, 8))]
        baris.append(" ".join(kata).capitalize() + ".")
    return {"judul": judul, "isi": "\n".join(baris), "kategori": rng.choice(KATEGORI),
            "keyword": ", ".join(rng.sample(KATA, 4)), "bait": "1", "gambar_file": "",
            "visibilitas": events.VISIBILITY_PUBLIC}


def panggil(url: str, token: str, data: dict | None = None, key: str | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Idempotency-Key", key)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def isi_antrean() -> dict[str, int]:
    conn = connect(
        os.environ["RABBITMQ_HOST"],
        int(os.environ.get("RABBITMQ_PORT", "5672")),
        os.environ["RABBITMQ_USER"],
        os.environ["RABBITMQ_PASSWORD"],
        os.environ.get("RABBITMQ_VHOST", "/"),
    )
    try:
        ch = conn.channel()
        return {q: ch.queue_declare(q, passive=True).method.message_count for q in ANTREAN}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed puisi lewat API poem")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--user", type=int, default=99)
    parser.add_argument("--parallel", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=2400, help="batas total (detik)")
    args = parser.parse_args()
    token = create_token(user_id=args.user, username="contoh", nama="Contoh",
                         expires_minutes=120)
    mulai = time.time()

    def lalu() -> str:
        return f"{time.time() - mulai:.0f} detik"

    def kirim(i: int) -> int | None:
        for percobaan in range(3):
            try:
                return panggil(API, token, buat_puisi(i), f"seed-{args.user}-{i}")["id_puisi"]
            except Exception as exc:
                if percobaan == 2:
                    print(f"gagal puisi {i}: {exc}", file=sys.stderr, flush=True)
                time.sleep(2)
        return None

    ids: list[int] = []
    with ThreadPoolExecutor(args.parallel) as pool:
        for pid in pool.map(kirim, range(args.count)):
            if pid is not None:
                ids.append(pid)
                if len(ids) % 200 == 0:
                    print(f"terkirim {len(ids)}/{args.count} ({lalu()})", flush=True)
    print(f"semua terkirim: {len(ids)}/{args.count} ({lalu()})", flush=True)

    belum = set(ids)
    sisa: dict[str, int] = {}
    lapor = time.time()
    while time.time() - mulai < args.timeout:
        for pid in list(belum):
            try:
                if panggil(f"{API}/{pid}", token)["puisi"]["status"] == "done":
                    belum.discard(pid)
            except Exception:
                pass
        sisa = {q: n for q, n in isi_antrean().items() if n}
        if not belum and not sisa:
            break
        if time.time() - lapor >= 60:
            print(f"menunggu: {len(belum)} belum done, antrean {sisa} ({lalu()})", flush=True)
            lapor = time.time()
        time.sleep(3)

    if belum or sisa or len(ids) < args.count:
        sys.exit(f"BELUM SELESAI: terkirim {len(ids)}, belum done {len(belum)}, "
                 f"antrean {sisa} ({lalu()})")
    print(f"SELESAI: {len(ids)} puisi dianalisis, antrean kosong ({lalu()})")


if __name__ == "__main__":
    main()
