# Implementation Plan: Penyelesaian kbbi-linker

## Overview
Menyelesaikan implementasi `kbbi-linker` yang sudah berjalan agar memenuhi kontrak event, ketahanan importer, observability, dan verifikasi container sesuai dokumen arsitektur.

## Architecture Decisions
- MySQL tetap menjadi sumber kebenaran; Redis tetap cache fail-open.
- Kontrak publik tetap `GET /api/kbbi/{poem_id}` dan queue `kbbi.q` tanpa alias wiki.
- Dataset tidak dimasukkan ke repositori; importer harus aman dijalankan ulang untuk versi yang sama.

## Task List

### Phase 1: Correctness
- [x] Perketat validasi amplop event dan tambah tes regresi.
- [x] Jadikan re-import dataset versioned bebas dari definisi basi dan tambah tes.

### Checkpoint: Correctness
- [x] Tes fokus `kbbi-linker` lulus.

### Phase 2: Observability
- [x] Terapkan log JSON dengan `event_id` dan `correlation_id` pada consumer.
- [x] Verifikasi metrik, health, dan kontrak API tetap lulus.

### Phase 3: Integration
- [ ] Verifikasi Docker image (compile dan Compose config sudah lulus; daemon tidak tersedia).
- [ ] Jalankan uji integrasi container (terblokir karena Docker daemon tidak tersedia).
- [x] Perbarui dokumentasi hasil verifikasi dan review final.

## Risks and Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Docker daemon tidak tersedia | Uji integrasi tertunda | Tetap verifikasi unit, compile, dan Compose config; laporkan blocker eksplisit |
| Dataset berlisensi tidak tersedia | Definisi produksi tidak dapat diuji | Gunakan fixture legal sintetis; jangan commit dataset |
| Perubahan shared memengaruhi layanan lain | Regresi lintas layanan | Batasi perubahan perilaku ke paket `kbbi-linker` dan jalankan suite yang tersedia |

## Open Questions
- Tidak ada; dokumen arsitektur telah menetapkan kontrak modul.
