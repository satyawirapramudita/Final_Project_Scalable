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

Skrip ini mem-build image layanan lebih dulu (kecuali --skip-build): `docker
compose up -d` TIDAK mem-build ulang, sehingga tanpa langkah itu uji bisa
berjalan di image lama dan hasilnya tidak mencerminkan kode HEAD.

Dua mode yang saling melengkapi:
  - cache aktif (default)  : yang diukur pengalaman pengguna dengan micro-cache
    gateway; hit ratio dicetak per tahap. Pada mix kueri populer, angka ini
    adalah kapasitas nginx, bukan kapasitas search.
  - --no-gateway-cache     : setiap request mencapai search; angka ini yang
    layak dikutip sebagai kapasitas layanan search.

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
    build,
    k6,
    k6_container_name,
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


def _gateway_url() -> str:
    """Alamat gateway dari .env (GATEWAY_PORT), default 8080."""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("GATEWAY_PORT="):
                return f"http://localhost:{line.split('=', 1)[1].strip()}"
    except OSError:
        pass
    return "http://localhost:8080"


GATEWAY = _gateway_url()


def set_gateway(rate: str | None, cache: str | None) -> None:
    """Recreate gateway. None = kembali ke nilai dari .env."""
    env = {}
    if rate:
        env.update({"API_RATE_LIMIT": rate, "API_BURST": "10000"})
    if cache:
        env["API_CACHE"] = cache
    run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "gateway"], env=env)
    wait_healthy(["puisi-gateway"])


def probe_rate_limit(token: str, n: int = 120, workers: int = 8) -> None:
    """Pastikan rate limit gateway benar-benar mati sebelum uji beban.

    Bila override API_RATE_LIMIT/API_BURST gagal diterapkan, sebagian request
    akan dijawab 429 dan seluruh hasil uji jadi tidak valid. Lebih baik gagal
    cepat dengan pesan jelas daripada menghasilkan tabel yang menyesatkan.

    Request dikirim paralel: probe sekuensial bisa lebih lambat dari rate limit
    (mis. 60 request @ ~50 ms = 20 r/s) sehingga 429 tidak pernah muncul dan
    pemeriksaan lolos secara palsu.
    """
    import concurrent.futures
    import urllib.error
    import urllib.request

    def one(_: int) -> int:
        req = urllib.request.Request(
            f"{GATEWAY}/api/search?q=senja", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except OSError:
            return 0

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        codes = list(pool.map(one, range(n)))
    rate = n / max(time.monotonic() - started, 0.001)
    rejected = codes.count(429)
    if rejected:
        raise RuntimeError(
            f"Rate limit gateway masih aktif: {rejected}/{n} request -> 429 "
            f"(~{rate:.0f} req/s). Override API_RATE_LIMIT/API_BURST tidak berlaku; "
            "hasil uji tidak valid. Periksa `docker compose up -d --force-recreate gateway`."
        )
    print(f"  probe rate limit: {n} request @ ~{rate:.0f} req/s -> 0 x 429 (uji valid)")


def print_gateway_net() -> None:
    """Tampilkan sysctl jaringan gateway: bukti mitigasi TIME_WAIT benar aktif."""
    for key in ("net.ipv4.tcp_tw_reuse", "net.ipv4.ip_local_port_range"):
        path = "/proc/sys/" + key.replace(".", "/")
        value = run(["docker", "compose", "exec", "-T", "gateway", "cat", path],
                    check=False).strip()
        print(f"  gateway {key} = {value or '(tidak terbaca)'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stages", type=int, nargs="+", default=[1, 5, 10, 20, 40, 80, 160, 320])
    ap.add_argument("--duration", default="20s", help="lama pengukuran per tahap")
    ap.add_argument("--warmup", default="5s", help="pemanasan per tahap, tidak dihitung")
    ap.add_argument("--replicas", type=int, default=2, help="jumlah replika search selama uji")
    ap.add_argument("--no-gateway-cache", action="store_true", help="API_CACHE=off selama uji")
    ap.add_argument("--user", type=int, default=99, help="user pemilik data seed")
    ap.add_argument("--skip-build", action="store_true",
                    help="jangan build image search (hanya bila yakin image sudah terbaru)")
    args = ap.parse_args()

    cache_on = not args.no_gateway_cache
    token = mint_token(args.user)
    print(f"Stress test search: tahap {args.stages} VU, {args.duration}/tahap, "
          f"{args.replicas} replika, cache gateway {'aktif' if cache_on else 'mati'}")
    if not args.skip_build:
        build("search")
    set_gateway("100000r/s", None if cache_on else "off")
    probe_rate_limit(token)
    print_gateway_net()
    names = scale("search", args.replicas)
    time.sleep(12)  # resolver gateway valid=10s: semua replika ikut dirotasi

    rows = []
    try:
        for vus in args.stages:
            k6(SCRIPT, token, vus, args.warmup, f"stress-warm{vus}")
            cpu: dict = {}
            sampler = threading.Thread(target=sample_cpu, args=(cpu, parse_seconds(args.duration)))
            sampler.start()
            res = k6(SCRIPT, token, vus, args.duration, f"stress-{vus}")
            sampler.join()
            res["vus"] = vus
            res["cpu_search"] = [cpu.get(n) for n in names]
            res["cpu_mysql"] = cpu.get("puisi-mysql")
            res["cpu_gateway"] = cpu.get("puisi-gateway")
            res["cpu_redis"] = cpu.get("puisi-redis")
            res["cpu_k6"] = cpu.get(k6_container_name(f"stress-{vus}"))
            res["slo_ok"] = (
                res["p95_ms"] < SLO_P95_MS
                and res["p99_ms"] < SLO_P99_MS
                and res["error_rate"] < SLO_ERROR
            )
            rows.append(res)
            print(f"  {vus:>4} VU: {res['rps']:>7} r/s  p50 {res['p50_ms']:>6} ms  "
                  f"p95 {res['p95_ms']:>6} ms  p99 {res['p99_ms']:>6} ms  "
                  f"err {res['error_rate']:>6.2%}  "
                  f"[2xx {res['resp_2xx']} 4xx {res['resp_4xx']} 5xx {res['resp_5xx']} "
                  f"conn {res['resp_conn_error']} hit {res['cache_hit_rate']:.0%}]  "
                  f"{'ok' if res['slo_ok'] else 'MELANGGAR'}")
    finally:
        print("Mengembalikan gateway (rate limit dan cache dari .env)...")
        set_gateway(None, None)

    peak = max(rows, key=lambda r: r["rps"])
    # Titik jenuh: tahap pertama yang throughput-nya sudah dalam 5% puncak.
    # (Definisi lama ">=90% puncak" selalu menunjuk tahap puncak itu sendiri,
    # sehingga menyesatkan, mis. "jenuh di 10 VU" padahal datar sampai 80 VU.)
    knee = next((r for r in rows if r["rps"] >= 0.95 * peak["rps"]), peak)
    broken = next((r for r in rows if not r["slo_ok"]), None)

    print(f"\n| VU | Throughput (r/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error | 2xx/4xx/5xx/conn | cache hit | CPU search (%) | CPU gateway (%) | CPU MySQL (%) | CPU Redis (%) | CPU k6 (%) | SLO |")
    print("|---:|---:|---:|---:|---:|---:|---|---:|---|---:|---:|---:|---:|---|")
    for r in rows:
        cpus = ", ".join(f"{c:.0f}" for c in r["cpu_search"] if c is not None)
        print(f"| {r['vus']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} "
              f"| {r['error_rate']:.2%} "
              f"| {r['resp_2xx']}/{r['resp_4xx']}/{r['resp_5xx']}/{r['resp_conn_error']} "
              f"| {r['cache_hit_rate']:.0%} | {cpus} "
              f"| {r['cpu_gateway'] or 0:.0f} | {r['cpu_mysql'] or 0:.0f} "
              f"| {r['cpu_redis'] or 0:.0f} "
              f"| {r['cpu_k6'] or 0:.0f} "
              f"| {'ok' if r['slo_ok'] else '**melanggar**'} |")
    print(f"\nPuncak {peak['rps']} r/s pada {peak['vus']} VU; mulai {knee['vus']} VU "
          f"throughput sudah dalam 5% puncak ({knee['rps']} r/s).")
    print(f"Titik patah SLO: {broken['vus']} VU" if broken else "SLO terpenuhi di semua tahap.")

    # Petunjuk penyebab kegagalan — ini yang membedakan "server penuh" dari
    # "gateway menolak" (429) dan "koneksi ke upstream putus" (5xx/conn).
    if any(r["resp_4xx"] for r in rows):
        print("  Catatan: ada respons 4xx — periksa apakah rate limit gateway masih aktif (429).")
    if any(r["resp_5xx"] for r in rows):
        print("  Catatan: ada respons 5xx — gateway gagal menghubungi upstream "
              "(kemungkinan habis ephemeral port / upstream tidak siap).")
    if any(r["resp_conn_error"] for r in rows):
        print("  Catatan: ada kegagalan koneksi level klien (status 0) — "
              "periksa batas koneksi/port, bukan kapasitas aplikasi.")
    if cache_on and all(r["cache_hit_rate"] > 0.9 for r in rows):
        print("  Catatan: cache hit >90% — angka ini mengukur kapasitas micro-cache gateway, "
              "bukan layanan search. Bandingkan dengan --no-gateway-cache.")

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
