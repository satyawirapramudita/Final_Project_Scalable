"""Stress test bertahap: cari titik jenuh dan titik patah SLO pencarian (Bab 10.5).

Jumlah pengguna virtual (VU) dinaikkan bertahap, misalnya 1, 5, 10, ... 320.
Setiap VU mengirim request berikutnya begitu jawaban datang (closed-loop), dengan
campuran query dari loadtest/search.js (60% kata populer, 30% kombinasi acak
yang menembus cache, 10% dengan filter kategori dan halaman).

Untuk setiap tahap dicatat throughput, P50/P95/P99, error, dan CPU tiap
komponen. Sebuah tahap MELANGGAR SLO bila P95 >= 300 ms, P99 >= 500 ms, atau
error >= 1% (lihat docs/slo.md). Dari tabel itu terbaca:
  - titik jenuh: tahap tempat throughput berhenti naik, dan
  - titik patah: tahap pertama yang melanggar SLO.

Prasyarat sama dengan scale_test.py: stack menyala, data search di-seed untuk
user 99, image grafana/k6 tersedia. Rate limit gateway dinaikkan selama uji
(semua beban datang dari satu IP) lalu dikembalikan.

Pemakaian (dari akar repo):
  python loadtest/stress_test.py                          # 2 replika, cache gateway aktif
  python loadtest/stress_test.py --no-gateway-cache       # pembanding tanpa micro-cache
  python loadtest/stress_test.py --stages 1 10 40 160 --duration 20s --replicas 3
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scale_test import (  # noqa: E402
    RESULTS,
    ROOT,
    k6,
    mint_token,
    parse_seconds,
    run,
    sample_cpu,
    scale,
    wait_healthy,
)

SCRIPT = "loadtest/search.js"
SLO_P95_MS = 300
SLO_P99_MS = 500
SLO_ERROR = 0.01


def set_gateway(rate: str | None, cache: str | None) -> None:
    """Recreate gateway. None = kembali ke nilai dari .env."""
    env = {}
    if rate:
        env.update({"API_RATE_LIMIT": rate, "API_BURST": "10000"})
    if cache:
        env["API_CACHE"] = cache
    run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "gateway"], env=env)
    wait_healthy(["puisi-gateway"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stages", type=int, nargs="+", default=[1, 5, 10, 20, 40, 80, 160, 320])
    ap.add_argument("--duration", default="20s", help="lama pengukuran per tahap")
    ap.add_argument("--warmup", default="5s", help="pemanasan per tahap, tidak dihitung")
    ap.add_argument("--replicas", type=int, default=2, help="jumlah replika search selama uji")
    ap.add_argument("--no-gateway-cache", action="store_true", help="API_CACHE=off selama uji")
    ap.add_argument("--user", type=int, default=99, help="user pemilik data seed")
    args = ap.parse_args()

    cache_on = not args.no_gateway_cache
    token = mint_token(args.user)
    print(f"Stress test search: tahap {args.stages} VU, {args.duration}/tahap, "
          f"{args.replicas} replika, cache gateway {'aktif' if cache_on else 'mati'}")
    set_gateway("100000r/s", None if cache_on else "off")
    names = scale("search", args.replicas)
    time.sleep(12)  # resolver gateway valid=10s: semua replika ikut dirotasi

    rows = []
    try:
        for vus in args.stages:
            k6(SCRIPT, token, vus, args.warmup, f"stress-warm{vus}")
            cpu: dict = {}
            sampler = threading.Thread(target=sample_cpu, args=(cpu, parse_seconds(args.duration) / 2))
            sampler.start()
            res = k6(SCRIPT, token, vus, args.duration, f"stress-{vus}")
            sampler.join()
            res["vus"] = vus
            res["cpu_search"] = [cpu.get(n) for n in names]
            res["cpu_mysql"] = cpu.get("puisi-mysql")
            res["cpu_gateway"] = cpu.get("puisi-gateway")
            res["cpu_redis"] = cpu.get("puisi-redis")
            res["slo_ok"] = (
                res["p95_ms"] < SLO_P95_MS
                and res["p99_ms"] < SLO_P99_MS
                and res["error_rate"] < SLO_ERROR
            )
            rows.append(res)
            print(f"  {vus:>4} VU: {res['rps']:>7} r/s  p50 {res['p50_ms']:>6} ms  "
                  f"p95 {res['p95_ms']:>6} ms  p99 {res['p99_ms']:>6} ms  "
                  f"error {res['error_rate']:.2%}  {'ok' if res['slo_ok'] else 'MELANGGAR'}")
    finally:
        print("Mengembalikan gateway (rate limit dan cache dari .env)...")
        set_gateway(None, None)

    peak = max(rows, key=lambda r: r["rps"])
    # titik jenuh: tahap pertama yang mencapai >= 90% throughput puncak
    knee = next(r for r in rows if r["rps"] >= 0.9 * peak["rps"])
    broken = next((r for r in rows if not r["slo_ok"]), None)

    print(f"\n| VU | Throughput (r/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error | CPU search (%) | CPU gateway (%) | CPU MySQL (%) | SLO |")
    print("|---:|---:|---:|---:|---:|---:|---|---:|---:|---|")
    for r in rows:
        cpus = ", ".join(f"{c:.0f}" for c in r["cpu_search"] if c is not None)
        print(f"| {r['vus']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} "
              f"| {r['error_rate']:.2%} | {cpus} | {r['cpu_gateway'] or 0:.0f} | {r['cpu_mysql'] or 0:.0f} "
              f"| {'ok' if r['slo_ok'] else '**melanggar**'} |")
    print(f"\nPuncak {peak['rps']} r/s pada {peak['vus']} VU; jenuh mulai sekitar {knee['vus']} VU.")
    print(f"Titik patah SLO: {broken['vus']} VU" if broken else "SLO terpenuhi di semua tahap.")

    RESULTS.mkdir(parents=True, exist_ok=True)
    tag = "cache" if cache_on else "nocache"
    out = RESULTS / f"stress-search-{tag}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({
        "service": "search", "replicas": args.replicas, "gateway_cache": cache_on,
        "duration": args.duration, "slo": {"p95_ms": SLO_P95_MS, "p99_ms": SLO_P99_MS, "error_rate": SLO_ERROR},
        "peak_vus": peak["vus"], "knee_vus": knee["vus"],
        "broken_vus": broken["vus"] if broken else None, "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"Hasil: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
