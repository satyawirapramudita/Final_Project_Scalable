"""Ringkasan per fase dari keluaran CSV k6 (PROFILE=kelas).

    python loadtest/laporan_stress.py k6.csv

"Mulai kewalahan" = tahap ramp pertama dengan p95 > 1000 ms atau error > 1 persen.
Ambang 1000 ms (bukan SLO 300 ms) karena latensi diukur dari laptop, termasuk
perjalanan ke us-east-1.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict

BATAS_P95_MS = 1000.0
BATAS_ERROR = 0.01
URUTAN = ["baseline", "ramp-20", "ramp-40", "ramp-80", "ramp-160", "ramp-320",
          "jeda", "spike", "pulih"]


def persentil(nilai: list[float], p: float) -> float:
    urut = sorted(nilai)
    return urut[max(0, math.ceil(p / 100 * len(urut)) - 1)]


def ringkas(baris: list[tuple]) -> dict:
    waktu = [b[0] for b in baris]
    durasi = max(waktu) - min(waktu) + 1
    latensi = [b[1] for b in baris]
    gagal = sum(1 for b in baris if not 200 <= b[2] < 300)
    return {"n": len(baris), "rps": len(baris) / durasi, "p95": persentil(latensi, 95),
            "p99": persentil(latensi, 99), "error": gagal / len(baris),
            "n429": sum(1 for b in baris if b[2] == 429)}


def sehat(r: dict) -> bool:
    return r["p95"] <= BATAS_P95_MS and r["error"] <= BATAS_ERROR


def baca(path: str) -> dict[str, list[tuple]]:
    """fase -> [(detik, latensi_ms, status, nama_endpoint)]."""
    fase: dict[str, list[tuple]] = defaultdict(list)
    with open(path, encoding="utf-8", newline="") as sumber:
        for row in csv.DictReader(sumber):
            if row["metric_name"] != "http_req_duration":
                continue
            tag = dict(p.split("=", 1) for p in (row.get("extra_tags") or "").split("&") if "=" in p)
            fase[row.get("fase") or tag.get("fase", "?")].append(
                (int(float(row["timestamp"])), float(row["metric_value"]),
                 int(row["status"] or 0), row["name"]))
    return fase


def main() -> None:
    parser = argparse.ArgumentParser(description="Ringkasan uji beban per fase")
    parser.add_argument("csv")
    parser.add_argument("--jendela", type=int, default=5, help="jendela hitung recovery (detik)")
    args = parser.parse_args()
    fase = baca(args.csv)
    hasil = {f: ringkas(fase[f]) for f in URUTAN if fase.get(f)}
    if not hasil:
        raise SystemExit("Tidak ada data fase di CSV.")

    print(f"{'Fase':<10}{'Request':>9}{'req/s':>8}{'p95 ms':>9}{'p99 ms':>9}{'Error':>8}{'429':>7}")
    for nama, r in hasil.items():
        print(f"{nama:<10}{r['n']:>9}{r['rps']:>8.0f}{r['p95']:>9.0f}{r['p99']:>9.0f}"
              f"{r['error']:>8.1%}{r['n429']:>7}")

    ramp = [(int(f.split('-')[1]), r) for f, r in hasil.items() if f.startswith("ramp-")]
    if ramp:
        jebol = next((i for i, (_, r) in enumerate(ramp) if not sehat(r)), None)
        if jebol is None:
            print(f"\nKapasitas: sampai {ramp[-1][0]} VU belum kewalahan "
                  f"(p95 <= {BATAS_P95_MS:.0f} ms, error <= {BATAS_ERROR:.0%}).")
        else:
            aman = f"{ramp[jebol - 1][0]} VU" if jebol else f"di bawah {ramp[0][0]} VU"
            print(f"\nMulai kewalahan di {ramp[jebol][0]} VU; masih sehat di {aman}.")
        print(f"Throughput tertinggi: {max(r['rps'] for _, r in ramp):.0f} req/s.")

    if fase.get("pulih"):
        data = fase["pulih"]
        awal = min(b[0] for b in data)
        jendela: dict[int, list[tuple]] = defaultdict(list)
        for b in data:
            jendela[(b[0] - awal) // args.jendela].append(b)
        # Jendela ujung berisi segelintir request sisa; satu yang lambat bukan berarti belum pulih.
        minimal = len(data) / len(jendela) / 4
        jendela = {k: v for k, v in jendela.items() if len(v) >= minimal}
        buruk = [k for k, v in jendela.items() if not sehat(ringkas(v))]
        if not buruk:
            print("Pulih dari spike: sehat sejak jendela pertama.")
        elif max(buruk) == max(jendela):
            print("Pulih dari spike: BELUM pulih sampai fase berakhir.")
        else:
            print(f"Pulih dari spike: ±{(max(buruk) + 1) * args.jendela} detik "
                  "(termasuk 10 detik penurunan beban).")

    print("\nPer endpoint (seluruh uji):")
    per: dict[str, list[tuple]] = defaultdict(list)
    for data in fase.values():
        for b in data:
            per[b[3]].append(b)
    for nama, data in sorted(per.items()):
        r = ringkas(data)
        print(f"  {nama:<26}{r['n']:>8} request  p95 {r['p95']:>6.0f} ms  error {r['error']:.1%}")


if __name__ == "__main__":
    main()
