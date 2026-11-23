// Uji beban pencarian (k6). Target SLO: p95 < 500 ms, error < 1% (lihat docs/slo.md).
// Target ambisius p95 < 300 ms dibaca dari ringkasan k6 (sengaja bukan threshold:
// threshold yang gagal membuat seluruh uji dinyatakan gagal).
//
// Siapkan data dulu (seed 1000 puisi untuk user 99):
//   Get-Content scripts/publish_fake_events.py | docker compose exec -T search python - --count 1000 --user 99
// Buat token untuk user 99:
//   Get-Content scripts/mint_token.py | docker compose exec -T poem python - 99     (atau pakai shared.security.create_token)
//
// Jalankan dari luar instance, ke gateway. Gateway membatasi 20 r/s per IP secara default,
// jadi naikkan dulu di .env:  API_RATE_LIMIT=5000r/s  API_BURST=1000  lalu recreate gateway.
//
//   k6 run -e BASE_URL=http://<host>:8080 -e TOKEN=<jwt> loadtest/search.js
//   k6 run -e VUS=50 -e DURATION=60s ...
//
// Tanpa k6 terpasang (jaringan Compose, langsung ke service search, tanpa gateway):
//   docker run --rm -i --network puisi-scalable_edge -e BASE_URL=http://search:8004 -e TOKEN=<jwt> grafana/k6 run - < loadtest/search.js

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const TOKEN = __ENV.TOKEN;

export const options = {
  vus: Number(__ENV.VUS || 20),
  duration: __ENV.DURATION || '30s',
  // Body respons tidak dipakai (check hanya membaca status + header), jadi
  // jangan diunduh: tanpa ini k6 menghabiskan ~100 µs CPU per request dan
  // menjadi bottleneck pengukuran (terbukti: k6 840% CPU vs gateway 198%).
  discardResponseBodies: true,
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.01'],
  },
};

const errors = new Rate('errors');
// Rincian status: tanpa ini, "error 52%" tidak bisa dibedakan antara
// rate limit (4xx), gateway/upstream gagal (5xx), atau koneksi putus (0).
const resp2xx = new Counter('resp_2xx');
const resp4xx = new Counter('resp_4xx');
const resp5xx = new Counter('resp_5xx');
const respConnError = new Counter('resp_conn_error');
// Hit ratio micro-cache gateway: memisahkan "dilayani cache" dari "sampai ke
// layanan search". Tanpa angka ini, throughput hanya mencerminkan nginx.
const cacheHits = new Rate('gateway_cache_hit');

// Campuran kueri. Catatan: kardinalitasnya kecil (7 populer + 900 pasangan +
// 450 kombinasi kategori), jadi pada beban tinggi hampir semua request tetap
// kena micro-cache gateway. Untuk mengukur kapasitas search yang sebenarnya,
// jalankan stress_test.py dengan --no-gateway-cache (lihat hit ratio per tahap).
const POPULAR = ['senja', 'laut', 'rindu', 'hujan malam', 'bintang', 'angin', 'cinta'];
const VOCAB = ['senja', 'laut', 'rindu', 'hujan', 'malam', 'bintang', 'angin', 'embun', 'bulan', 'sunyi',
  'kenangan', 'pagi', 'cakrawala', 'ombak', 'pantai', 'gunung', 'sungai', 'daun', 'kabut', 'cahaya',
  'bayang', 'mimpi', 'harapan', 'luka', 'cinta', 'waktu', 'langit', 'awan', 'doa', 'fajar'];
const KATEGORI = ['Alam', 'Cinta', 'Sedih', 'Rindu', 'Harapan'];

function pick(list) { return list[Math.floor(Math.random() * list.length)]; }

export function setup() {
  if (!TOKEN) throw new Error('Isi TOKEN: k6 run -e TOKEN=<jwt> ...');
}

export default function () {
  let query;
  const roll = Math.random();
  if (roll < 0.6) query = `q=${encodeURIComponent(pick(POPULAR))}`;
  else if (roll < 0.9) query = `q=${pick(VOCAB)}+${pick(VOCAB)}`;
  else query = `q=${pick(VOCAB)}&kategori=${pick(KATEGORI)}&page=${1 + Math.floor(Math.random() * 3)}&limit=5`;

  const res = http.get(`${BASE_URL}/api/search?${query}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    tags: { name: 'GET /api/search' },
  });
  const ok = check(res, { 'status 200': (r) => r.status === 200 });
  errors.add(!ok);

  if (res.status === 0) respConnError.add(1);
  else if (res.status < 300) resp2xx.add(1);
  else if (res.status < 500) resp4xx.add(1);
  else resp5xx.add(1);
  cacheHits.add(res.headers['X-Cache-Status'] === 'HIT');
}
