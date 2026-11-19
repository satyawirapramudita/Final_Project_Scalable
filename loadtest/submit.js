// Uji beban submit puisi (k6) — memvalidasi idempotency key saat stress test.
//
// Skenario:
//   1. submit-normal  : setiap VU submit puisi unik (Idempotency-Key unik per iterasi).
//   2. submit-duplikat: key yang SAMA dikirim ulang berulang — harus 201 replay dengan
//      id_puisi identik (tandai Idempotent-Replay: true) dan TIDAK membuat puisi baru.
//   3. key-bentrok    : key sama dengan payload BEDA — harus 409.
//
// Jalankan dari luar instance, ke gateway. Naikkan rate limit dulu seperti search.js.
//   k6 run -e BASE_URL=http://<host>:8080 -e TOKEN=<jwt> loadtest/submit.js
//   k6 run -e VUS=50 -e DURATION=60s -e SCENARIO=submit-duplikat ...
//
// Tanpa k6 terpasang (jaringan Compose, langsung ke service poem):
//   docker run --rm -i --network puisi-scalable_edge -e BASE_URL=http://poem:8002 -e TOKEN=<jwt> grafana/k6 run - < loadtest/submit.js

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const TOKEN = __ENV.TOKEN;
const SCENARIO = __ENV.SCENARIO || 'submit-normal';

export const options = {
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || '30s',
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.01'],
  },
};

const errors = new Rate('errors');
const replays = new Counter('idempotent_replays');
const conflicts = new Counter('idempotent_conflicts');
// Kegagalan level koneksi/TCP (k6 status 0): request tidak pernah sampai ke
// aplikasi — tidak boleh dihitung sebagai perubahan id_puisi.
const edgeFailures = new Counter('edge_failures');

// Puisi contoh — cukup bervariasi agar payload unik untuk skenario normal.
const KATEGORI = ['Alam', 'Cinta', 'Sedih', 'Rindu', 'Harapan'];

export function setup() {
  if (!TOKEN) throw new Error('Isi TOKEN: k6 run -e TOKEN=<jwt> ...');
}

function uniqueKey() {
  return `k6-${SCENARIO}-${__VU}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export default function () {
  const params = {
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  };

  if (SCENARIO === 'submit-duplikat') {
    // Key TETAP per iterasi VU: submit sama dikirim 3x — id_puisi harus identik.
    const key = `k6-dup-${__VU}-${Date.now()}`;
    const body = JSON.stringify({
      judul: `Uji Duplikat VU ${__VU}`,
      isi: 'Baris pertama.\nBaris kedua.',
      kategori: KATEGORI[__VU % KATEGORI.length],
    });
    let firstId = null;
    let allSame = true;
    for (let i = 0; i < 3; i++) {
      const res = http.post(`${BASE_URL}/api/poems`, body, {
        ...params,
        headers: { ...params.headers, 'Idempotency-Key': key },
        tags: { name: 'POST /api/poems (duplikat)' },
      });
      if (res.status === 0) {
        // Koneksi gagal (reset/timeout TCP): hasil tidak pasti, request mungkin
        // sampai atau tidak. Hitung terpisah — BUKAN bukti id berubah.
        edgeFailures.add(1);
        errors.add(true);
        allSame = false;
        continue;
      }
      const ok = check(res, {
        'status 201': (r) => r.status === 201,
        'id_puisi konsisten': (r) => {
          const id = r.json('id_puisi');
          if (firstId === null) firstId = id;
          return id === firstId;
        },
      });
      if (res.headers['Idempotent-Replay'] === 'true') replays.add(1);
      errors.add(!ok);
      if (!ok) allSame = false;
    }
    if (!allSame) console.error(`VU ${__VU}: id_puisi berubah di tengah retry!`);
    return;
  }

  if (SCENARIO === 'key-bentrok') {
    // Key sama, payload beda → 409 Conflict.
    const key = `k6-bentrok-${__VU}-${Date.now()}`;
    const res1 = http.post(
      `${BASE_URL}/api/poems`,
      JSON.stringify({ judul: 'A', isi: 'Isi A', kategori: 'Alam' }),
      { ...params, headers: { ...params.headers, 'Idempotency-Key': key }, tags: { name: 'POST /api/poems (bentrok 1)' } },
    );
    const res2 = http.post(
      `${BASE_URL}/api/poems`,
      JSON.stringify({ judul: 'B', isi: 'Isi B', kategori: 'Alam' }),
      { ...params, headers: { ...params.headers, 'Idempotency-Key': key }, tags: { name: 'POST /api/poems (bentrok 2)' } },
    );
    const ok = check(res2, { 'status 409': (r) => r.status === 409 });
    if (res2.status === 409) conflicts.add(1);
    errors.add(!ok);
    return;
  }

  // Skenario default: submit-normal — tiap iterasi satu puisi unik.
  const res = http.post(
    `${BASE_URL}/api/poems`,
    JSON.stringify({
      judul: `Puisi Beban VU ${__VU} #${Date.now()}`,
      isi: 'Angin membawa kata\nmenyeberangi senja.',
      kategori: KATEGORI[Math.floor(Math.random() * KATEGORI.length)],
    }),
    { ...params, headers: { ...params.headers, 'Idempotency-Key': uniqueKey() }, tags: { name: 'POST /api/poems' } },
  );
  const ok = check(res, { 'status 201': (r) => r.status === 201 });
  errors.add(!ok);
}
