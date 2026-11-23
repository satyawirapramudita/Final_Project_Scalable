// Uji beban word cloud lewat gateway (k6): membandingkan micro-cache gateway
// nyala vs mati (API_CACHE=api_cache / off). Setiap request tetap melewati
// pemeriksaan akses puisi (auth_request ke layanan poem), cache atau tidak.
//
// Butuh beberapa puisi publik yang sudah dianalisis; isi IDS dengan id-nya.
//   docker run --rm -i --network puisi-scalable_edge -e BASE_URL=http://gateway \
//     -e TOKEN=<jwt> -e IDS=12,13,14 grafana/k6 run - < loadtest/wordcloud.js
// Naikkan dulu rate limit gateway (API_RATE_LIMIT/API_BURST), lihat loadtest/search.js.

import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const TOKEN = __ENV.TOKEN;
const IDS = (__ENV.IDS || '').split(',').filter(Boolean);

export const options = {
  vus: Number(__ENV.VUS || 64),
  duration: __ENV.DURATION || '30s',
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.01'],
  },
};

const errors = new Rate('errors');
const cacheHits = new Rate('gateway_cache_hit');

export function setup() {
  if (!TOKEN || !IDS.length) throw new Error('Isi TOKEN dan IDS: k6 run -e TOKEN=<jwt> -e IDS=1,2,3 ...');
}

export default function () {
  const id = IDS[Math.floor(Math.random() * IDS.length)];
  const res = http.get(`${BASE_URL}/api/wordcloud/${id}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    tags: { name: 'GET /api/wordcloud/{id}' },
  });
  const ok = check(res, { 'status 200': (r) => r.status === 200 });
  errors.add(!ok);
  cacheHits.add(res.headers['X-Cache-Status'] === 'HIT');
}
