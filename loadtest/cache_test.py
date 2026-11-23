"""Bandingkan micro-cache gateway nyala vs mati pada /api/wordcloud/{id}.

Langkah: buat N puisi publik (lewat gateway) dan tunggu dianalisis, lalu untuk
setiap mode (API_CACHE=off, lalu api_cache) recreate gateway, warm-up, jalankan
loadtest/wordcloud.js dengan VU tetap, dan ambil sampel CPU. Puisi uji dihapus
dan gateway dikembalikan ke konfigurasi .env di akhir.

Pemakaian (dari akar repo, stack menyala):
  python loadtest/cache_test.py
  python loadtest/cache_test.py --poems 20 --vus 64 --duration 30s
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from scale_test import RESULTS, ROOT, parse_seconds, run, sample_cpu, set_gateway_limit, wait_healthy

GATEWAY = "http://localhost:8080"


def api(method: str, path: str, body=None, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(GATEWAY + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def make_poems(n: int) -> tuple[str, list[int]]:
    name = f"cachetest{random.randint(10000, 99999)}"
    api("POST", "/api/auth/register", {"username": name, "nama": "Cache Test", "password": "rahasia123"})
    token = api("POST", "/api/auth/login", {"username": name, "password": "rahasia123"})[1]["token"]
    words = "senja rindu dermaga sunyi cakrawala lembayung embun kabut fajar bulan".split()
    ids = []
    for i in range(n):
        isi = " ".join(random.choices(words, k=40))
        ids.append(api("POST", "/api/poems", {"judul": f"Cache {i}", "isi": isi, "kategori": "alam"}, token)[1]["id_puisi"])
    deadline = time.time() + 90
    pending = set(ids)
    while pending and time.time() < deadline:
        pending = {p for p in pending if api("GET", f"/api/wordcloud/{p}", token=token)[0] != 200}
        time.sleep(1)
    if pending:
        raise RuntimeError(f"Word cloud belum siap: {sorted(pending)}")
    return token, ids


def k6_wordcloud(token: str, ids: list[int], vus: int, duration: str) -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = "_k6-wordcloud.json"
    cmd = [
        "docker", "run", "--rm", "-i", "--network", "puisi-scalable_edge",
        # `z` = relabel SELinux container_file_t (lihat scale_test.py).
        "-v", f"{RESULTS.as_posix()}:/out:z",
        # k6 berjalan sebagai UID 12345 di image; tulis /out sebagai UID:GID
        # pemanggil (pemilik direktori hasil). Lihat scale_test.py.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "BASE_URL=http://gateway", "-e", f"TOKEN={token}",
        "-e", f"IDS={','.join(map(str, ids))}", "-e", f"VUS={vus}", "-e", f"DURATION={duration}",
        "grafana/k6", "run", "--quiet", "--summary-trend-stats", "avg,med,p(95),p(99),max",
        "--summary-export", f"/out/{out}", "-",
    ]
    with open(ROOT / "loadtest" / "wordcloud.js", encoding="utf-8") as f:
        proc = subprocess.run(cmd, cwd=ROOT, stdin=f, capture_output=True, text=True)
    path = RESULTS / out
    if not path.exists():
        raise RuntimeError(
            f"k6 tidak menulis {out} (exit {proc.returncode}). stderr:\n"
            f"{proc.stderr[-1500:]}"
        )
    m = json.loads(path.read_text(encoding="utf-8"))["metrics"]
    path.unlink()
    d = m["http_req_duration"]
    return {
        "rps": round(m["http_reqs"]["rate"], 1),
        "p50_ms": round(d["med"], 1),
        "p95_ms": round(d["p(95)"], 1),
        "p99_ms": round(d["p(99)"], 1),
        "error_rate": round(m["errors"]["value"], 4),
        "hit_rate": round(m["gateway_cache_hit"]["value"], 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--poems", type=int, default=20)
    ap.add_argument("--vus", type=int, default=64)
    ap.add_argument("--duration", default="30s")
    args = ap.parse_args()

    token, ids = make_poems(args.poems)
    print(f"{len(ids)} puisi uji siap; {args.vus} VU, {args.duration} per mode")
    rows = []
    try:
        for mode in ("off", "api_cache"):
            run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "gateway"],
                env={"API_RATE_LIMIT": "100000r/s", "API_BURST": "10000", "API_CACHE": mode})
            wait_healthy(["puisi-gateway"])
            k6_wordcloud(token, ids, args.vus, "10s")  # warm-up
            cpu: dict = {}
            sampler = threading.Thread(target=sample_cpu, args=(cpu, parse_seconds(args.duration)))
            sampler.start()
            res = k6_wordcloud(token, ids, args.vus, args.duration)
            sampler.join()
            res["cache"] = mode
            res["cpu_wordcloud"] = round(sum(v for k, v in cpu.items() if "wordcloud" in k), 1)
            res["cpu_poem"] = round(sum(v for k, v in cpu.items() if "-poem-" in k), 1)
            res["cpu_gateway"] = cpu.get("puisi-gateway")
            rows.append(res)
            print(f"  cache={mode}: {res['rps']} r/s, p95 {res['p95_ms']} ms, hit {res['hit_rate']:.0%}, "
                  f"CPU wordcloud {res['cpu_wordcloud']}% poem {res['cpu_poem']}%")
    finally:
        set_gateway_limit(None, None)
        for pid in ids:
            api("DELETE", f"/api/poems/{pid}", token=token)

    print("\n| Cache gateway | Throughput (r/s) | p50 (ms) | p95 (ms) | p99 (ms) | Hit rate | Error | CPU wordcloud (%) | CPU poem (%) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['cache']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | "
              f"{r['hit_rate']:.0%} | {r['error_rate']:.2%} | {r['cpu_wordcloud']} | {r['cpu_poem']} |")
    out = RESULTS / f"cache-wordcloud-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"vus": args.vus, "duration": args.duration, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nHasil: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
