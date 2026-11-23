"""Uji skalabilitas horizontal: throughput & latensi pada 1..N replika.

Untuk setiap jumlah replika, skrip ini:
  1. menskalakan layanan (docker compose up -d --scale <svc>=n),
  2. menunggu semua replika healthy + cache DNS gateway (valid=10s) kedaluwarsa,
  3. menjalankan k6 (container grafana/k6) LEWAT GATEWAY dengan VU tetap,
  4. mengambil sampel CPU tiap container di tengah uji (docker stats),
lalu mencetak tabel Markdown dan menyimpan hasil JSON di loadtest/results/.

Beban closed-loop: VU tetap yang selalu mengirim request berikutnya begitu
jawaban datang, jadi throughput = kapasitas maksimum sistem pada konfigurasi itu.

Prasyarat:
  - stack menyala dan data search sudah di-seed:
      docker compose exec -T search python - --count 1000 --user 99 < scripts/publish_fake_events.py
  - image grafana/k6 tersedia (docker pull grafana/k6)

Skrip ini mem-build image layanan lebih dulu (kecuali --skip-build), karena
`docker compose up -d` tidak mem-build ulang dan image lama akan membuat hasil
tidak mencerminkan kode HEAD.

Rate limit gateway (20 r/s per IP) dinaikkan sementara selama uji lalu
dikembalikan; semua beban k6 datang dari satu IP container.

Pemakaian (dari akar repo):
  python loadtest/scale_test.py                       # search, replika 1 2 3 4
  python loadtest/scale_test.py --replicas 1 2 4 --vus 64 --duration 30s
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "loadtest" / "results"
NETWORK = "puisi-scalable_edge"
SCRIPTS = {"search": "loadtest/search.js"}


def run(cmd: list[str], env: dict | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} gagal:\n{proc.stderr[-2000:]}")
    return proc.stdout


def set_gateway_limit(rate: str | None, burst: str | None) -> None:
    """Recreate gateway dengan rate limit tertentu (None = nilai dari .env)."""
    env = {}
    if rate:
        env = {"API_RATE_LIMIT": rate, "API_BURST": burst or "10000"}
    run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "gateway"], env=env)
    wait_healthy(["puisi-gateway"])


def replicas_of(service: str) -> list[str]:
    out = run(["docker", "compose", "ps", "--format", "{{.Name}}", service])
    return [n for n in out.split() if n]


def wait_healthy(names: list[str], limit: float = 120) -> None:
    deadline = time.time() + limit
    while time.time() < deadline:
        states = run(["docker", "inspect", "--format", "{{.State.Health.Status}}", *names]).split()
        if states and all(s == "healthy" for s in states):
            return
        time.sleep(2)
    raise RuntimeError(f"Container tidak healthy dalam {limit}s: {names}")


def scale(service: str, n: int) -> list[str]:
    # Tanpa --no-recreate: bila image baru saja di-build (kode terbaru), compose
    # harus recreate container agar image itu yang diuji. Container yang
    # konfigurasinya tidak berubah tidak disentuh.
    run(["docker", "compose", "up", "-d", "--scale", f"{service}={n}", service])
    names = replicas_of(service)
    if len(names) != n:
        raise RuntimeError(f"Diharapkan {n} replika {service}, ada {len(names)}")
    wait_healthy(names)
    print_running_image(names[0])
    return names


def build(service: str) -> None:
    """Build image layanan yang diuji.

    Penting: `docker compose up -d` TIDAK mem-build ulang. Tanpa langkah ini,
    uji bisa berjalan di image lama dan hasilnya tidak mencerminkan kode HEAD
    (pernah terjadi: metrik search_breaker_state tidak ada sama sekali).
    """
    print(f"  build image {service} agar kode terbaru yang diuji...")
    run(["docker", "compose", "build", service])
    print("  build selesai")


def print_running_image(name: str) -> None:
    """Bukti image yang benar-benar berjalan (bukan tebakan dari `compose images`)."""
    image = run(["docker", "inspect", "--format", "{{.Image}}", name], check=False).strip()
    info = run(
        ["docker", "image", "inspect", "--format", "{{.Id}} dibuat {{.Created}}", image],
        check=False,
    ).strip() if image else ""
    print(f"  container {name}: {info or '(image tidak terbaca)'}")


def mint_token(user_id: int) -> str:
    code = (
        "from shared.security import create_token;"
        f"print(create_token(user_id={user_id}, username='loadtest', nama='Loadtest'))"
    )
    return run(["docker", "compose", "exec", "-T", "poem", "python", "-c", code]).strip()


def sample_cpu(store: dict, duration: float, samples: int = 3) -> None:
    """Rata-rata `samples` pembacaan docker stats selama `duration` detik.

    Satu pembacaan (versi sebelumnya) sangat berisik: pada beban yang sama,
    CPU MySQL bisa terbaca 39% di satu tahap dan 0,3% di tahap lain. Rata-rata
    beberapa titik membuat angka bisa dipakai untuk menyimpulkan bottleneck.
    """
    interval = max(1.0, duration / (samples + 1))
    acc: dict[str, list[float]] = {}
    for _ in range(samples):
        time.sleep(interval)
        out = run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}}"],
            check=False,
        )
        for line in out.splitlines():
            name, _, cpu = line.partition(";")
            try:
                acc.setdefault(name, []).append(float(cpu.rstrip("%")))
            except ValueError:
                pass
    for name, values in acc.items():
        store[name] = round(sum(values) / len(values), 1)


def k6_container_name(tag: str) -> str:
    """Nama container k6 untuk tag uji ini (dipakai juga saat sampel CPU)."""
    return f"loadtest-k6-{tag}-{os.getpid()}"


def _counter(metrics: dict, name: str) -> int:
    return int(metrics.get(name, {}).get("count", 0))


def _metric_rate(metrics: dict, name: str) -> float:
    entry = metrics.get(name, {})
    return round(float(entry.get("value", entry.get("rate", 0.0))), 4)


def k6(script: str, token: str, vus: int, duration: str, tag: str) -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_name = f"_k6-{tag}.json"
    cmd = [
        # Nama tetap agar sampel CPU bisa memisahkan beban klien dari server.
        "docker", "run", "--rm", "-i", "--name", k6_container_name(tag), "--network", NETWORK,
        # `z` = relabel SELinux container_file_t (host enforcing seperti
        # Fedora/RHEL), tanpa ini k6 tidak bisa menulis --summary-export.
        "-v", f"{RESULTS.as_posix()}:/out:z",
        # Image k6 resmi berjalan sebagai UID 12345 (bukan root). Tanpa --user,
        # k6 tidak boleh menulis ke /out bila direktori hasil milik user lain
        # (mis. dibuat oleh `sudo python3`): "touch: Permission denied".
        # Dipakai UID:GID pemanggil, yang sekaligus pemilik direktori itu.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "BASE_URL=http://gateway", "-e", f"TOKEN={token}",
        "-e", f"VUS={vus}", "-e", f"DURATION={duration}",
        "grafana/k6", "run", "--quiet",
        "--summary-trend-stats", "avg,med,p(95),p(99),max",
        "--summary-export", f"/out/{out_name}", "-",
    ]
    with open(ROOT / script, encoding="utf-8") as f:
        # Threshold yang gagal membuat exit code != 0; hasil tetap dibaca.
        proc = subprocess.run(cmd, cwd=ROOT, stdin=f, capture_output=True, text=True,
                              env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    path = RESULTS / out_name
    if not path.exists():
        # Tanpa pemeriksaan ini, kegagalan k6 (direktori /out tidak bisa
        # ditulis, flag salah, image tidak ada) muncul sebagai
        # FileNotFoundError yang menyesatkan. Tampilkan stderr aslinya.
        raise RuntimeError(
            f"k6 tidak menulis {out_name} (exit {proc.returncode}). stderr:\n"
            f"{proc.stderr[-1500:]}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    m = data["metrics"]
    dur = m["http_req_duration"]
    return {
        "rps": round(m["http_reqs"]["rate"], 1),
        "requests": m["http_reqs"]["count"],
        "avg_ms": round(dur["avg"], 1),
        "p50_ms": round(dur["med"], 1),
        "p95_ms": round(dur["p(95)"], 1),
        "p99_ms": round(dur["p(99)"], 1),
        "error_rate": _metric_rate(m, "errors"),
        # Rincian kegagalan: 4xx (mis. 429 rate limit), 5xx (gateway/upstream),
        # 0 (koneksi putus). Tanpa ini, penyebab "error 52%" tidak terbaca.
        "resp_2xx": _counter(m, "resp_2xx"),
        "resp_4xx": _counter(m, "resp_4xx"),
        "resp_5xx": _counter(m, "resp_5xx"),
        "resp_conn_error": _counter(m, "resp_conn_error"),
        "cache_hit_rate": _metric_rate(m, "gateway_cache_hit"),
    }


def parse_seconds(duration: str) -> float:
    unit = duration[-1]
    value = float(duration[:-1])
    return value * {"s": 1, "m": 60}[unit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--service", default="search", choices=sorted(SCRIPTS))
    ap.add_argument("--replicas", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--vus", type=int, default=64)
    ap.add_argument("--duration", default="30s")
    ap.add_argument("--warmup", default="10s")
    ap.add_argument("--user", type=int, default=99, help="user pemilik data seed")
    ap.add_argument("--restore", type=int, default=2, help="jumlah replika setelah uji")
    ap.add_argument("--skip-build", action="store_true",
                    help="jangan build image layanan (dipakai hanya bila yakin image sudah terbaru)")
    args = ap.parse_args()

    svc = args.service
    token = mint_token(args.user)
    print(f"Uji skalabilitas {svc}: replika {args.replicas}, {args.vus} VU, {args.duration}/tingkat")
    if not args.skip_build:
        build(svc)
    set_gateway_limit("100000r/s", "10000")
    rows = []
    try:
        for n in args.replicas:
            names = scale(svc, n)
            time.sleep(12)  # resolver gateway valid=10s: pastikan replika baru ikut dirotasi
            k6(SCRIPTS[svc], token, args.vus, args.warmup, f"warm{n}")
            cpu: dict = {}
            sampler = threading.Thread(
                target=sample_cpu, args=(cpu, parse_seconds(args.duration))
            )
            sampler.start()
            res = k6(SCRIPTS[svc], token, args.vus, args.duration, f"run{n}")
            sampler.join()
            res["replicas"] = n
            res["cpu_replicas"] = [cpu.get(name) for name in names]
            res["cpu_mysql"] = cpu.get("puisi-mysql")
            res["cpu_gateway"] = cpu.get("puisi-gateway")
            res["cpu_redis"] = cpu.get("puisi-redis")
            res["cpu_k6"] = cpu.get(k6_container_name(f"run{n}"))
            rows.append(res)
            print(f"  {n} replika: {res['rps']} r/s, p95 {res['p95_ms']} ms, "
                  f"p99 {res['p99_ms']} ms, error {res['error_rate']:.2%}, "
                  f"CPU replika {res['cpu_replicas']} mysql {res['cpu_mysql']}")
    finally:
        print("Mengembalikan rate limit gateway dan jumlah replika...")
        set_gateway_limit(None, None)
        scale(svc, args.restore)

    base = rows[0]["rps"] or 1
    print(f"\n| Replika {svc} | Throughput (r/s) | Speedup | p50 (ms) | p95 (ms) | p99 (ms) | Error | CPU per replika (%) | CPU MySQL (%) |")
    print("|---:|---:|---:|---:|---:|---:|---:|---|---:|")
    for r in rows:
        cpus = ", ".join(f"{c:.0f}" for c in r["cpu_replicas"] if c is not None)
        print(f"| {r['replicas']} | {r['rps']} | {r['rps'] / base:.2f}x | {r['p50_ms']} | {r['p95_ms']} "
              f"| {r['p99_ms']} | {r['error_rate']:.2%} | {cpus} | {r['cpu_mysql'] or 0:.0f} |")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"scaling-{svc}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({
        "service": svc, "vus": args.vus, "duration": args.duration,
        "via": "gateway", "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nHasil: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
