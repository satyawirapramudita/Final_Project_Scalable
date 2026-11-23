"""Uji endpoint /metrics search, termasuk mode banyak worker uvicorn.

Dengan `UVICORN_WORKERS>1`, setiap worker punya registry Prometheus sendiri.
Endpoint /metrics menggabungkannya lewat PROMETHEUS_MULTIPROC_DIR; uji ini
memastikan (a) mode satu proses tetap jalan, dan (b) angka dari beberapa proses
benar-benar dijumlahkan, bukan hanya menampilkan satu worker.

Semua impor app dilakukan di subprocess: dalam satu proses pytest gabungan,
layanan lain (auth/poem) sudah mendaftarkan metrik bernama sama ke default
registry. Konflik itu tidak terjadi di produksi karena setiap layanan berjalan
di container sendiri.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SECRET = "test_secret_panjang_acak_minimal_32_karakter"


def _run(args: list[str], env_extra: dict) -> subprocess.CompletedProcess:
    """Jalankan kode Python di proses baru dengan environment bersih."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "JWT_SECRET": SECRET}
    env.pop("PROMETHEUS_MULTIPROC_DIR", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", *args], cwd=ROOT, capture_output=True, text=True, env=env
    )


SINGLE = """
from services.search.app.main import metrics
resp = metrics()
assert resp.status_code == 200, resp.status_code
assert b"# HELP" in resp.body
print("ok-single")
"""

WRITER = """
import sys
from prometheus_client import Counter
from services.search.app.main import metrics
Counter("mp_test_total", "uji multiprocess").inc(float(sys.argv[1]))
assert "mp_test_total" in metrics().body.decode()
"""

READER = """
from services.search.app.main import metrics
for line in metrics().body.decode().splitlines():
    if line.startswith("mp_test_total "):
        print(line)
        break
else:
    raise SystemExit("mp_test_total tidak ada di /metrics")
"""


def test_metrics_single_process() -> None:
    proc = _run([SINGLE], {})
    assert proc.returncode == 0, proc.stderr[-1000:]
    assert "ok-single" in proc.stdout


def test_metrics_multiprocess_workers(tmp_path: Path) -> None:
    """Dua proses menulis nilai; proses ketiga memeriksa hasil gabungannya."""
    env_extra = {"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}
    for value in ("1", "2"):
        proc = _run([WRITER, value], env_extra)
        assert proc.returncode == 0, proc.stderr[-1000:]

    proc = _run([READER], env_extra)
    assert proc.returncode == 0, proc.stderr[-1000:]
    # Dua worker masing-masing menambah 1 dan 2 -> gabungan harus 3.
    assert proc.stdout.strip() == "mp_test_total 3.0", proc.stdout
