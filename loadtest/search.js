// Uji beban pencarian (k6). Target SLO: p95 < 300 ms, error < 1%.
//
// Siapkan data dulu (seed 1000 puisi untuk user 99):
//   Get-Content scripts/publish_fake_events.py | docker compose exec -T search python - --count 1000 --user 99
// Buat token untuk user 99:
//   Get-Content scripts/mint_token.py | docker exec -i puisi-poem python - 99     (atau pakai shared.security.create_token)
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
import { Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const TOKEN = __ENV.TOKEN;

export const options = {
  vus: Number(__ENV.VUS || 20),
  duration: __ENV.DURATION || '30s',
  thresholds: {
    http_req_duration: ['p(95)<300'],
    errors: ['rate<0.01'],
  },
};

const errors = new Rate('errors');

// Campuran: kata populer (sering kena cache) dan kombinasi acak (menembus cache).
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
}
