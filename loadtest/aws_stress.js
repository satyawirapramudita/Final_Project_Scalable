// Uji beban untuk deployment AWS (ALB / CloudFront / EC2) dengan beberapa profil.
//
// Berbeda dari loadtest/search.js yang hanya satu profil, skrip ini bisa dipakai
// untuk smoke, ramp (cari titik jenuh), spike, soak, mixed, dan write-heavy.
// Semua request lewat BASE_URL (ALB/CloudFront/EIP), bukan nama service Docker.
//
// Contoh:
//   docker run --rm -i -v "$PWD/loadtest:/scripts:ro,z" \
//     -e BASE_URL=https://<domain> -e TOKEN=<jwt> -e PROFILE=smoke \
//     grafana/k6 run /scripts/aws_stress.js
//
//   PROFILE=ramp  MAX_VUS=160 STAGE_DURATION=30s
//   PROFILE=spike SPIKE_VUS=300
//   PROFILE=soak  VUS=50 SOAK_DURATION=30m
//   PROFILE=mixed VUS=40 DURATION=5m IDS=12,13,14
//   PROFILE=write VUS=20 DURATION=3m WRITE_RATIO=0.8
//   PROFILE=kelas IDS=12,13,14   (7,5 menit: baseline, ramp, spike, pulih, 1 replika poem mati;
//                                 jalankan lewat loadtest/kelas.sh)
//
// Token: pakai TOKEN (satu) atau TOKENS (dipisah koma) untuk banyak pengguna.
// Banyak token penting agar cache pencarian per pengguna tidak terlalu optimis.
//
// Rate limit gateway: di AWS gateway melihat IP ALB/CloudFront, jadi semua
// pengguna tampak satu IP. Naikkan API_RATE_LIMIT/API_BURST sementara sebelum uji
// (lihat docs/Stress-Test-AWS.md), lalu kembalikan setelah selesai.

import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/+$/, '');
const PROFILE = (__ENV.PROFILE || 'smoke').toLowerCase();
const TOKENS = (__ENV.TOKENS || __ENV.TOKEN || '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
const IDS = (__ENV.IDS || '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const VUS = intEnv('VUS', 40);
const DURATION = __ENV.DURATION || '5m';
const MAX_VUS = intEnv('MAX_VUS', 320);
const STAGE_DURATION = __ENV.STAGE_DURATION || '30s';
const SPIKE_VUS = intEnv('SPIKE_VUS', 300);
const SOAK_DURATION = __ENV.SOAK_DURATION || '30m';
const P95_MS = intEnv('P95_MS', 300);
const P99_MS = intEnv('P99_MS', 500);
const ERROR_RATE = floatEnv('ERROR_RATE', 0.01);
const WRITE_RATIO = floatEnv('WRITE_RATIO', 0.8);

function intEnv(name, fallback) {
  const value = Number(__ENV[name]);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

function floatEnv(name, fallback) {
  const value = Number(__ENV[name]);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
}

// Tahapan ramp: 1, 5, 10, 20, 40, 80, 160, 320 (dipotong MAX_VUS), lalu tahan.
function rampStages() {
  const steps = [1, 5, 10, 20, 40, 80, 160, 320].filter((v) => v <= MAX_VUS);
  if (steps[steps.length - 1] !== MAX_VUS) steps.push(MAX_VUS);
  const stages = steps.map((target) => ({ duration: STAGE_DURATION, target }));
  stages.push({ duration: '60s', target: MAX_VUS });
  stages.push({ duration: '15s', target: 0 });
  return stages;
}

// Profil kelas: [detik, VU tujuan, nama fase]. SCALE < 1 memendekkan semua durasi (untuk uji skrip).
const SCALE = floatEnv('SCALE', 1);
const KELAS = [
  [30, 10, 'baseline'],
  ...[20, 40, 80, 160, 320].flatMap((v) => [[10, v, 'transisi'], [30, v, `ramp-${v}`]]),
  [10, 10, 'transisi'],
  [10, 10, 'jeda'],
  [10, 300, 'spike'],
  [60, 300, 'spike'],
  [10, 10, 'pulih'],
  [30, 10, 'pulih'],
  [10, 20, 'transisi'],
  [80, 20, 'gagal'], // loadtest/kelas.sh mematikan 1 dari 2 replika poem di fase ini
];

function faseSekarang() {
  let batas = 0;
  const lewat = exec.instance.currentTestRunDuration / 1000;
  for (const [detik, , nama] of KELAS) {
    batas += Math.max(1, Math.round(detik * SCALE));
    if (lewat < batas) return nama;
  }
  return 'selesai';
}

const strictThresholds = {
  http_req_duration: [`p(95)<${P95_MS}`, `p(99)<${P99_MS}`],
  http_req_failed: [`rate<${ERROR_RATE}`],
};

function optionsForProfile() {
  const base = {
    discardResponseBodies: true,
    summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
  };

  switch (PROFILE) {
    case 'kelas':
      // Tanpa threshold: hasilnya dibaca per fase oleh loadtest/laporan_stress.py.
      return {
        ...base,
        scenarios: {
          kelas: {
            executor: 'ramping-vus',
            startVUs: 10,
            stages: KELAS.map(([detik, target]) => ({
              duration: `${Math.max(1, Math.round(detik * SCALE))}s`,
              target,
            })),
            gracefulRampDown: '5s',
          },
        },
      };
    case 'smoke':
      return { ...base, vus: 1, duration: '30s', thresholds: strictThresholds };
    case 'ramp':
      // Tanpa threshold: tujuannya menemukan titik patah, bukan lulus/gagal.
      return {
        ...base,
        scenarios: {
          ramp: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: rampStages(),
            gracefulRampDown: '10s',
          },
        },
      };
    case 'spike':
      return {
        ...base,
        scenarios: {
          spike: {
            executor: 'ramping-vus',
            startVUs: 10,
            stages: [
              { duration: '60s', target: 10 },
              { duration: '10s', target: SPIKE_VUS },
              { duration: '60s', target: SPIKE_VUS },
              { duration: '10s', target: 10 },
              { duration: '60s', target: 10 },
            ],
            gracefulRampDown: '10s',
          },
        },
      };
    case 'soak':
      return {
        ...base,
        scenarios: {
          soak: { executor: 'constant-vus', vus: VUS, duration: SOAK_DURATION },
        },
        thresholds: strictThresholds,
      };
    case 'mixed':
      return {
        ...base,
        scenarios: {
          mixed: { executor: 'constant-vus', vus: VUS, duration: DURATION },
        },
        thresholds: strictThresholds,
      };
    case 'write':
      return {
        ...base,
        scenarios: {
          write: { executor: 'constant-vus', vus: VUS, duration: DURATION },
        },
        thresholds: strictThresholds,
      };
    default:
      throw new Error(
        `PROFILE tidak dikenal: ${PROFILE}. Pilih kelas|smoke|ramp|spike|soak|mixed|write`,
      );
  }
}

export const options = optionsForProfile();

const errors = new Rate('errors');
const resp2xx = new Counter('resp_2xx');
const resp4xx = new Counter('resp_4xx');
const resp5xx = new Counter('resp_5xx');
const respConnError = new Counter('resp_conn_error');
const cacheHit = new Rate('gateway_cache_hit');

const POPULAR = ['senja', 'laut', 'rindu', 'hujan malam', 'bintang', 'angin', 'cinta'];
const VOCAB = [
  'senja', 'laut', 'rindu', 'hujan', 'bintang', 'angin', 'cinta', 'embun',
  'sunyi', 'kenangan', 'pagi', 'malam', 'pantai', 'gunung', 'bulan', 'bunga',
];
const KATEGORI = ['Alam', 'Cinta', 'Sedih', 'Rindu', 'Harapan'];

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function record(res) {
  const status = res.status;
  const ok = status >= 200 && status < 300;
  errors.add(!ok);

  if (status >= 200 && status < 300) resp2xx.add(1);
  else if (status >= 400 && status < 500) resp4xx.add(1);
  else if (status >= 500) resp5xx.add(1);
  else respConnError.add(1);

  const cache = res.headers['X-Cache-Status'];
  if (cache) cacheHit.add(cache === 'HIT');
}

export function setup() {
  if (!TOKENS.length) {
    throw new Error('TOKEN atau TOKENS wajib diisi (JWT dari user yang punya data seed).');
  }
  return { tokens: TOKENS, baseUrl: BASE_URL, profile: PROFILE };
}

export default function (data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const headers = { Authorization: `Bearer ${token}` };

  if (PROFILE === 'kelas') return kelas(headers);
  if (PROFILE === 'write') return writeOrSearch(headers);
  if (PROFILE === 'mixed') return mixed(headers);
  return search(headers);
}

function search(headers) {
  const roll = Math.random();
  let query;
  if (roll < 0.6) {
    query = `q=${encodeURIComponent(pick(POPULAR))}`;
  } else if (roll < 0.9) {
    query = `q=${pick(VOCAB)}+${pick(VOCAB)}`;
  } else {
    query = `q=${pick(VOCAB)}&kategori=${pick(KATEGORI)}&page=${1 + Math.floor(Math.random() * 3)}&limit=5`;
  }

  const res = http.get(`${BASE_URL}/api/search?${query}`, {
    headers,
    tags: { name: 'GET /api/search' },
  });
  record(res);
  check(res, { 'search 200': (r) => r.status === 200 });
}

function listPoems(headers) {
  const res = http.get(`${BASE_URL}/api/poems?limit=20&page=1`, {
    headers,
    tags: { name: 'GET /api/poems' },
  });
  record(res);
  check(res, { 'list 200': (r) => r.status === 200 });
}

function getWordcloud(headers) {
  const id = pick(IDS);
  const res = http.get(`${BASE_URL}/api/wordcloud/${id}`, {
    headers,
    tags: { name: 'GET /api/wordcloud/{id}' },
  });
  record(res);
  check(res, { 'wordcloud 200': (r) => r.status === 200 });
}

function getKbbi(headers) {
  const id = pick(IDS);
  const res = http.get(`${BASE_URL}/api/kbbi/${id}`, {
    headers,
    tags: { name: 'GET /api/kbbi/{id}' },
  });
  record(res);
  check(res, { 'kbbi 200': (r) => r.status === 200 });
}

function mixed(headers) {
  const roll = Math.random();
  if (roll < 0.6) return search(headers);
  if (roll < 0.8) return listPoems(headers);
  if (IDS.length && roll < 0.9) return getWordcloud(headers);
  if (IDS.length) return getKbbi(headers);
  return search(headers);
}

// 50% search, 20% daftar puisi, 10% wordcloud, 10% kbbi, 10% submit.
function kelas(headers) {
  exec.vu.metrics.tags.fase = faseSekarang();
  const roll = Math.random();
  if (roll < 0.5) return search(headers);
  if (roll < 0.7) return listPoems(headers);
  if (roll < 0.8) return getWordcloud(headers);
  if (roll < 0.9) return getKbbi(headers);
  return submitPoem(headers);
}

function writeOrSearch(headers) {
  if (Math.random() < WRITE_RATIO) return submitPoem(headers);
  return search(headers);
}

function submitPoem(headers) {
  const body = JSON.stringify({
    judul: `Stress AWS ${__VU}-${__ITER}-${Date.now()}`,
    isi: 'Baris uji beban AWS.\nBaris kedua untuk mengisi kolom isi.',
    kategori: pick(KATEGORI),
  });
  const key = `aws-${__VU}-${__ITER}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const res = http.post(`${BASE_URL}/api/poems`, body, {
    headers: {
      ...headers,
      'Content-Type': 'application/json',
      'Idempotency-Key': key,
    },
    tags: { name: 'POST /api/poems' },
  });
  record(res);
  check(res, { 'submit 201': (r) => r.status === 201 });
}
