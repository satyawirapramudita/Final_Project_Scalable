"""Uji kebocoran puisi privat, end-to-end lewat gateway.

Dua pengguna uji dibuat (token dicetak langsung dengan JWT_SECRET, tanpa login):
A (pemilik) dan B (pengguna lain). A membuat satu puisi privat dan satu publik,
lalu skrip memeriksa bahwa B TIDAK bisa membaca, mencari, atau mengambil word
cloud/KBBI puisi privat, sedangkan A tetap bisa. Terakhir puisi publik dijadikan
privat dan aksesnya diperiksa lagi. Puisi uji dihapus di akhir.

Dijalankan di dalam container poem (punya shared/, JWT_SECRET, dan jaringan ke gateway):

    Get-Content scripts/check_visibility.py | docker compose exec -T poem python -

Keluar dengan kode 1 bila ada pemeriksaan yang gagal.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import sys
import time
import urllib.error
import urllib.request

from shared.security import create_token

BASE_URL = os.getenv("CHECK_BASE_URL", "http://gateway").rstrip("/")
USER_A, USER_B = 990001, 990002
TIMEOUT = 45  # detik menunggu event (indeks search, analisis nlp)

results: list[tuple[bool, str]] = []


def call(method: str, path: str, token: str | None, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw) if raw else {}
        except ValueError:
            return err.code, {}


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"  [{'LULUS' if ok else 'GAGAL'}] {label}", flush=True)


def search_ids(token: str, word: str) -> set[int]:
    status, data = call("GET", f"/api/search?q={word}", token)
    return {item["id"] for item in data.get("items", [])} if status == 200 else set()


def wait_until(condition, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(1)
    return False


def unique_word() -> str:
    # Kata acak huruf kecil: praktis tidak mungkin cocok dengan puisi lain.
    return "uji" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(10))


def create(token: str, judul: str, word: str, visibilitas: str) -> int:
    status, data = call(
        "POST",
        "/api/poems",
        token,
        {
            "judul": judul,
            "kategori": "Uji",
            "isi": f"{word} senja di cakrawala\nembun pagi yang syahdu",
            "visibilitas": visibilitas,
        },
    )
    if status != 201:
        sys.exit(f"Gagal membuat puisi uji ({status}): {data}")
    return int(data["id_puisi"])


def main() -> int:
    a = create_token(user_id=USER_A, username="uji_pemilik", nama="Uji Pemilik")
    b = create_token(user_id=USER_B, username="uji_lain", nama="Uji Lain")
    word_priv, word_pub = unique_word(), unique_word()

    print(f"Gateway: {BASE_URL}")
    priv = create(a, "Puisi Rahasia (uji)", word_priv, "private")
    pub = create(a, "Puisi Terbuka (uji)", word_pub, "public")
    print(f"Puisi privat #{priv}, publik #{pub}. Menunggu indeks search dan analisis...")

    try:
        indexed = wait_until(lambda: priv in search_ids(a, word_priv) and pub in search_ids(b, word_pub))
        analyzed = wait_until(
            lambda: call("GET", f"/api/poems/{priv}", a)[1].get("puisi", {}).get("status") == "done"
        )
        if not indexed:
            print("  (peringatan: indeks search belum terisi dalam batas waktu)")
        if not analyzed:
            print("  (peringatan: analisis nlp belum selesai; word cloud bisa 404 'belum tersedia')")

        print("\nPengguna lain (B) terhadap puisi PRIVAT:")
        check(call("GET", f"/api/poems/{priv}", b)[0] == 404, "B membuka puisi privat -> 404")
        check(priv not in search_ids(b, word_priv), "B mencari kata unik puisi privat -> tidak ada")
        check(call("GET", f"/api/wordcloud/{priv}", b)[0] == 404, "B mengambil word cloud puisi privat -> 404")
        check(call("GET", f"/api/kbbi/{priv}", b)[0] == 404, "B mengambil KBBI puisi privat -> 404")

        print("\nPemilik (A) terhadap puisi PRIVAT:")
        check(call("GET", f"/api/poems/{priv}", a)[0] == 200, "A membuka puisi privatnya -> 200")
        check(priv in search_ids(a, word_priv), "A menemukan puisi privatnya di pencarian")
        wc_status = call("GET", f"/api/wordcloud/{priv}", a)[0]
        check(wc_status == 200 or (wc_status == 404 and not analyzed), f"A mengambil word cloud -> {wc_status}")
        check(call("GET", f"/api/kbbi/{priv}", a)[0] == 200, "A mengambil KBBI -> 200")

        print("\nPengguna lain (B) terhadap puisi PUBLIK:")
        check(call("GET", f"/api/poems/{pub}", b)[0] == 200, "B membuka puisi publik -> 200")
        check(pub in search_ids(b, word_pub), "B menemukan puisi publik di pencarian")
        check(call("GET", f"/api/kbbi/{pub}", b)[0] == 200, "B mengambil KBBI puisi publik -> 200")

        print("\nPuisi publik dijadikan PRIVAT:")
        status, _ = call("PUT", f"/api/poems/{pub}", a, {"visibilitas": "private"})
        check(status == 200, "A mengubah visibilitas -> 200")
        check(call("GET", f"/api/poems/{pub}", b)[0] == 404, "B langsung tidak bisa membuka -> 404")
        check(call("GET", f"/api/wordcloud/{pub}", b)[0] == 404, "B langsung tidak bisa ambil word cloud -> 404")
        check(
            wait_until(lambda: pub not in search_ids(b, word_pub), timeout=15),
            "B tidak lagi menemukannya di pencarian (dalam 15 detik)",
        )

        print("\nTanpa token dan path aneh:")
        check(call("GET", f"/api/wordcloud/{priv}", None)[0] == 401, "word cloud tanpa token -> 401")
        check(call("GET", "/api/kbbi/bukan-angka", b)[0] == 404, "path KBBI tanpa id angka -> 404")
    finally:
        for poem_id in (priv, pub):
            call("DELETE", f"/api/poems/{poem_id}", a)
        print(f"\nPuisi uji #{priv} dan #{pub} dihapus.")

    failed = [label for ok, label in results if not ok]
    print(f"\nHasil: {len(results) - len(failed)}/{len(results)} lulus.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
