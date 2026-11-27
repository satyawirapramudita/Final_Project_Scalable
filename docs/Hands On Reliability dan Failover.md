# **Hands On Reliability dan Failover**

![][image1]

### **1\. Tujuan Pembelajaran**

1. Mahasiswa mampu mengidentifikasi titik rawan kegagalan (*Single Point of Failure / SPOF*) dan jalur kritis pada desain arsitektur *microservices* dan *event-driven* yang telah dibuat.  
2. Mahasiswa mampu menganalisis perilaku sistem saat terjadi kegagalan komponen dan merancang strategi *failover*.  
3. Mahasiswa mampu mengimplementasikan pola-pola ketahanan terdistribusi (*Fault Tolerance Patterns*)

### **2\. Deskripsi & Skenario**

Sistem yang *scalable* tidak hanya dituntut mampu menangani lonjakan beban trafik yang masif, tetapi juga wajib tetap hidup (*resilient*) saat komponen perangkat lunak maupun perangkat keras di dalamnya mengalami kegagalan.

Pada hands-on ini, setiap kelompok diminta melakukan **audit kritis** terhadap rancangan arsitektur sistem masing-masing, menelusuri titik-titik proses yang rawan melumpuhkan sistem (*cascading failure*), lalu menerapkan rekayasa perbaikan arsitektural dan teknologi agar sistem memiliki mekanisme pemulihan otomatis (*failover*) yang andal.

### **3\. Tahapan Pengerjaan Tugas**

#### **Tahap 1: Pemetaan Titik Rawan & Audit SPOF (Review Arsitektur Eksisting)**

Mahasiswa mengaudit diagram arsitektur tim masing-masing dan memilih minimal **3 titik kritis** untuk dianalisis. Berikut ini referensi kluster yang sebaiknya direview:

1. **Interface / Gateway Layer:** Apakah API Gateway yang digunakan masih berupa *single instance* yang menjadi SPOF? Misalnya apa yang terjadi jika *gateway* mengalami kehabisan memori (*OOM*)?  
2. **Synchronous Communication Path:** Identifikasi rantai pemanggilan sinkron (REST / gRPC) antar-layanan. Jika satu layanan pendukung melambat drastis apakah hal itu memicu penumpukan antrean koneksi (*thread exhaustion*) yang merembet ke layanan lain (*cascading failure*)?  
3. **Asynchronous & Broker Path:** Apa yang terjadi jika salah satu *Consumer Worker/Subscriber worker*  mengalami *crash* di tengah pemrosesan pesan? Apakah pesan hilang, tertahan selamanya, atau menyebabkan pesan bermasalah yang mematikan (down) *worker* secara berulang?  
4. **Data Layer:** Bagaimana ketersediaan basis data jika node basis data utama (*primary*) mati mendadak?

#### **Tahap 2: Perancangan Solusi Perbaikan (Engineering Patterns)**

Untuk setiap titik kritis yang diidentifikasi, tim wajib merancang dan menerapkan solusi perbaikan yang dapat berbasis pola berikut (tak terbatas pada):

* Menerapkan *Circuit Breaker* (3 status: *Closed*, *Open*, *Half-Open*) pada jalur komunikasi sinkron.  
* Menentukan ambang batas (*error threshold*) dan sediakan mekanisme *fallback*.  
* memasang mekanisme *retry* pada koneksi database.  
* Deploy minimal 2 replika kontainer untuk layanan-layanan kritis di balik *Load Balancer* dengan *health check* aktif (Active-Active).  
* Rancang mekanisme *failover* pada layer data misal: pemanfaatan *Read Replicas*.  
* Menerapkan pemeriksaan *Idempotency Key* dengan UUID pada *Consumer* untuk mencegah duplikasi data di basis data.  
* Menggunakan mekanisme *Dead Letter Queue* di broker untuk pesan yang gagal diproses berulang kali.

### **3\. Format Laporan**

1. Tabel Analisis Risiko (daftar titik kritis, risiko kegagalan, dan dampak terhadap *Availability)*.  
2. Diagram Arsitektur yang Diperbaiki:  
3. Rincian Solusi Teknis

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnAAAAAECAYAAAAOPwJdAAAANElEQVR4Xu3WMQ0AMAwEseAOoJIpqGYvgrzkwcshuLqnHwAAOeoPAADsZuAAAMIYOACAMAOTpGslhjl7rwAAAABJRU5ErkJggg==> "horizontal line"