// ─── Konfigurasi ─────────────────────────────────────────────────────────────
// Base URL CloudFront/Lambda untuk image generator.
// Ganti dengan URL distribusi CloudFront Anda, contoh:
//   "https://d1234abcd.cloudfront.net"
// Kosongkan ("") untuk dev lokal — preview akan diganti placeholder dan submit
// tetap bisa dilakukan tanpa gambar (gambar_file = "").
const GENERATOR_BASE_URL = window.location.origin;

// Kosongkan: kita panggil Function URL Lambda langsung, bukan lewat path CloudFront /generate-puisi
const GENERATE_PATH = "/fungsi";

// ─── Helpers ─────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function showMessage(el, text, isError = false) {
    el.textContent = text;
    el.classList.toggle("error", Boolean(text) && isError);
}

// ─── Token ────────────────────────────────────────────────────────────────────
// JWT disimpan di localStorage dan dikirim via header Authorization: Bearer.
// Semua layanan memverifikasi token secara lokal — restart auth tidak logout.
const TOKEN_KEY = "puisi_token";

function getToken() {
    return localStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
}

async function api(path, method = "GET", body = null) {
    const options = { method, headers: {} };
    const token = getToken();
    if (token) options.headers["Authorization"] = `Bearer ${token}`;
    if (body !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
    }
    // Endpoint berbasis path lewat gateway: /api/auth/*, /api/poems, dst.
    const res = await fetch(`/api/${path}`, options);
    const data = await res.json().catch(() => ({}));
    return { status: res.status, data };
}

// ─── Sesi ────────────────────────────────────────────────────────────────────
async function checkSession() {
    const { data } = await api("auth/me");
    if (data.ok) {
        enterApp(data.nama, data.username);
    } else {
        setToken("");
        showAuth();
    }
}

// Simpan nama penulis aktif agar preview bisa menggunakannya
let _currentNama = "";
let _currentUsername = "";

function enterApp(nama, username) {
    _currentNama = nama;
    _currentUsername = username;
    $("session-info").textContent = `Sesi aktif: ${nama} (@${username})`;
    $("btn-logout").classList.remove("hidden");
    $("auth-panel").classList.add("hidden");
    $("app-panel").classList.remove("hidden");
    loadPuisi();
}

function showAuth() {
    _currentNama = "";
    _currentUsername = "";
    $("session-info").textContent = "Belum login";
    $("btn-logout").classList.add("hidden");
    $("auth-panel").classList.remove("hidden");
    $("app-panel").classList.add("hidden");
    $("grid-puisi").replaceChildren();
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
async function login(e) {
    e.preventDefault();
    showMessage($("auth-message"), "");
    const { status, data } = await api("auth/login", "POST", {
        username: $("login-username").value,
        password: $("login-password").value,
    });
    if (status === 200 && data.ok) {
        $("form-login").reset();
        setToken(data.token);
        enterApp(data.nama, data.username);
    } else {
        showMessage($("auth-message"), data.message || "Login gagal", true);
    }
}

async function register(e) {
    e.preventDefault();
    showMessage($("auth-message"), "");
    const { status, data } = await api("auth/register", "POST", {
        username: $("reg-username").value,
        nama: $("reg-nama").value,
        password: $("reg-password").value,
    });
    if (status === 201 && data.ok) {
        $("form-register").reset();
        showMessage($("auth-message"), data.message + ". Silakan login.", false);
        switchTab("login");
    } else {
        showMessage($("auth-message"), data.message || "Register gagal", true);
    }
}

async function logout() {
    setToken("");
    await api("auth/logout", "POST");
    showAuth();
    showMessage($("auth-message"), "Anda telah logout.", false);
}

// ─── Image Generator ──────────────────────────────────────────────────────────

/**
 * Bangun URL ke Lambda generator.
 * @param {object} params - { template, judul, penulis, bait, save }
 * @returns {string} URL lengkap
 */
// Lambda kita menerima template sebagai angka ("1".."4"), sedangkan pilihan di
// index.html masih pakai nama file ("template1.jpg" dst). Petakan di sini agar
// index.html tidak perlu diubah.
function mapTemplateToNumber(templateValue) {
    const match = /(\d+)/.exec(templateValue || "");
    return match ? match[1] : "1";
}

function buildGeneratorUrl(params) {
    const base = (GENERATOR_BASE_URL || "").replace(/\/$/, "");
    const qs = new URLSearchParams({
        template: mapTemplateToNumber(params.template),
        judul: params.judul || "",
        penulis: params.penulis || "",
        bait: params.bait || "",
        // Lambda kita pakai mode=preview / mode=save, bukan save=true/false
        mode: params.save ? "save" : "preview",
    });
    if (params.save) {
        // Nonce agar tidak ada cache lama yang terlayani untuk permintaan save
        qs.set("_t", Date.now());
    }
    return `${base}${GENERATE_PATH}?${qs.toString()}`;
}

/**
 * Memanggil Lambda dengan save=false (preview).
 * Return: data:image/jpeg;base64,... atau null jika gagal/tidak dikonfigurasi.
 */
async function fetchPreview(params) {
    if (!GENERATOR_BASE_URL) return null;
    try {
        const url = buildGeneratorUrl(params);
        const res = await fetch(url);
        if (!res.ok) return null;
        const contentType = res.headers.get("Content-Type") || "";
        if (contentType.startsWith("image/")) {
            // Lambda mengembalikan body base64 di respons Function URL:
            // jika cloudfront meneruskan body as-is, gunakan blob
            const blob = await res.blob();
            return URL.createObjectURL(blob);
        }
        // Fallback: coba parse JSON (misal Lambda mengembalikan base64 di JSON)
        const json = await res.json().catch(() => null);
        if (json && json.image) return `data:image/jpeg;base64,${json.image}`;
        return null;
    } catch {
        return null;
    }
}

/**
 * Memanggil Lambda dengan save=true dan return filename.
 * Return: string filename atau null jika gagal/tidak dikonfigurasi.
 */
async function saveImage(params) {
    if (!GENERATOR_BASE_URL) return null;
    try {
        const url = buildGeneratorUrl({ ...params, save: true });
        const res = await fetch(url);
        if (!res.ok) return null;
        const json = await res.json().catch(() => null);
        // Lambda kita mengembalikan { success, key, bucket, ... } bukan { ok, filename }
        return (json && json.success && json.key) ? json.key : null;
    } catch {
        return null;
    }
}

/** Kumpulkan parameter form untuk generator */
function getGeneratorParams() {
    const templateEl = document.querySelector("input[name='template']:checked");
    return {
        template: templateEl ? templateEl.value : "template1.jpg",
        judul: $("puisi-judul").value.trim(),
        penulis: _currentNama || _currentUsername || "Anonim",
        bait: $("puisi-bait").value.trim(),
    };
}

// Debounce timer untuk preview
let _previewTimer = null;

function schedulePreview() {
    clearTimeout(_previewTimer);
    _previewTimer = setTimeout(updatePreview, 400);
}

async function updatePreview() {
    const params = getGeneratorParams();
    const previewEl = $("preview-gambar");
    const statusEl = $("preview-status");

    // Jangan panggil kalau data belum cukup
    if (!params.judul || !params.bait) {
        previewEl.style.display = "none";
        statusEl.textContent = params.judul || params.bait
            ? "Isi judul dan bait untuk melihat preview."
            : "";
        return;
    }

    if (!GENERATOR_BASE_URL) {
        // Dev lokal: tampilkan pesan placeholder
        previewEl.style.display = "none";
        statusEl.textContent = "[Lokal] GENERATOR_BASE_URL belum diatur — preview tidak tersedia.";
        return;
    }

    statusEl.textContent = "Memuat preview...";
    previewEl.style.display = "none";

    const src = await fetchPreview(params);
    if (src) {
        previewEl.src = src;
        previewEl.style.display = "block";
        statusEl.textContent = "";
    } else {
        statusEl.textContent = "Gagal memuat preview gambar.";
    }
}

// ─── Submit Puisi ─────────────────────────────────────────────────────────────
async function submitPuisi(e) {
    e.preventDefault();
    showMessage($("puisi-message"), "");

    const submitBtn = $("btn-submit-puisi");
    submitBtn.disabled = true;
    submitBtn.value = "Memproses...";

    try {
        const params = getGeneratorParams();
        const bait = $("puisi-bait").value.trim();

        // Langkah 1: panggil Lambda save=true untuk mendapat gambar_file
        let gambar_file = "";
        if (GENERATOR_BASE_URL && params.judul && bait) {
            showMessage($("puisi-message"), "Membuat gambar puisi...");
            gambar_file = (await saveImage(params)) || "";
        }

        // Langkah 2: kirim metadata ke backend EC2
        showMessage($("puisi-message"), "Menyimpan puisi...");
        const { status, data } = await api("poems", "POST", {
            judul: $("puisi-judul").value.trim(),
            kategori: $("puisi-kategori").value.trim(),
            keyword: $("puisi-keyword").value.trim(),
            isi: $("puisi-isi").value.trim(),
            bait: bait,
            gambar_file: gambar_file,
        });

        if (status === 201 && data.ok) {
            $("form-puisi").reset();
            $("preview-gambar").style.display = "none";
            $("preview-status").textContent = "";
            showMessage(
                $("puisi-message"),
                data.message || "Puisi berhasil dikirim, menunggu analisis.",
                false
            );
            loadPuisi();
        } else {
            showMessage($("puisi-message"), data.message || "Gagal mengirim puisi", true);
            if (status === 401) {
                setToken("");
                showAuth();
            }
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.value = "Kirim Puisi";
    }
}

// ─── Daftar Puisi (Grid Kartu) ────────────────────────────────────────────────
async function loadPuisi() {
    const { status, data } = await api("poems");
    if (status === 200 && data.ok) {
        const grid = $("grid-puisi");
        grid.replaceChildren();

        if (data.puisi.length === 0) {
            const p = document.createElement("p");
            p.className = "empty";
            p.textContent = "Belum ada puisi. Unggah puisi pertama Anda!";
            grid.appendChild(p);
            return true;
        }

        for (const p of data.puisi) {
            const card = document.createElement("div");
            card.className = "puisi-card";

            // Gambar thumbnail atau placeholder
            const img = document.createElement("img");
            img.alt = `Gambar puisi: ${p.judul}`;
            if (p.gambar_url) {
                img.src = p.gambar_url;
                img.onerror = () => {
                    // Jika gambar gagal dimuat, tampilkan placeholder teks
                    img.style.display = "none";
                    const ph = document.createElement("div");
                    ph.className = "puisi-card-placeholder";
                    ph.textContent = p.judul.charAt(0).toUpperCase();
                    card.insertBefore(ph, card.firstChild);
                };
            } else {
                // Tidak ada URL gambar (dev lokal atau puisi lama)
                img.style.display = "none";
                const ph = document.createElement("div");
                ph.className = "puisi-card-placeholder";
                ph.textContent = p.judul.charAt(0).toUpperCase();
                card.appendChild(ph);
            }
            card.appendChild(img);

            // Info bawah kartu
            const info = document.createElement("div");
            info.className = "puisi-card-info";

            const judul = document.createElement("strong");
            judul.textContent = p.judul;
            info.appendChild(judul);

            const tgl = document.createElement("small");
            const tglStr = p.tgl_submit ? new Date(p.tgl_submit).toLocaleDateString("id-ID") : "-";
            tgl.textContent = tglStr;
            info.appendChild(tgl);

            const kat = document.createElement("small");
            kat.textContent = p.kategori || "";
            info.appendChild(kat);

            const hint = document.createElement("small");
            hint.textContent = "Klik untuk lihat isi";
            hint.style.fontStyle = "italic";
            info.appendChild(hint);

            card.appendChild(info);

            // Detail isi puisi — halaman putih sederhana di bawah gambar, hidden by default
            const detail = document.createElement("div");
            detail.className = "puisi-detail hidden";
            const isiText = document.createElement("div");
            isiText.className = "puisi-isi";
            isiText.textContent = p.isi || "(Isi puisi kosong)";
            detail.appendChild(isiText);
            if (p.bait) {
                const baitText = document.createElement("div");
                baitText.className = "puisi-bait-text";
                baitText.textContent = `Bait: ${p.bait}`;
                detail.appendChild(baitText);
            }
            card.appendChild(detail);

            // Tombol Word Cloud + area kanvas
            const wcBtn = document.createElement("button");
            wcBtn.type = "button";
            wcBtn.textContent = "☁ Word Cloud";
            wcBtn.className = "wc-btn";
            const wcContainer = document.createElement("div");
            wcContainer.className = "wc-container hidden";
            const wcCanvas = document.createElement("canvas");
            wcCanvas.width = 280;
            wcCanvas.height = 200;
            wcCanvas.className = "wc-canvas";
            wcContainer.appendChild(wcCanvas);
            wcBtn.addEventListener("click", (ev) => {
                ev.stopPropagation(); // jangan toggle detail
                wcContainer.classList.toggle("hidden");
                if (!wcContainer.classList.contains("hidden") && !wcCanvas.dataset.loaded) {
                    renderWordCloud(p.id, wcCanvas, wcBtn);
                }
            });
            card.appendChild(wcBtn);
            card.appendChild(wcContainer);

            card.style.cursor = "pointer";
            card.title = "Klik untuk lihat / sembunyikan isi";
            card.addEventListener("click", () => {
                detail.classList.toggle("hidden");
            });

            grid.appendChild(card);
        }
        return true;
    } else if (status === 401) {
        setToken("");
        showAuth();
    }
    return false;
}

function flashMessage() {
    const message = $("puisi-message");
    showMessage(message, "Daftar puisi diperbarui.");
    message.classList.remove("flash");
    void message.offsetWidth;
    message.classList.add("flash");
    setTimeout(() => message.classList.remove("flash"), 900);
}

async function refreshPuisi() {
    const button = $("btn-refresh");
    button.disabled = true;
    button.textContent = "Memuat...";
    try {
        if (await loadPuisi()) flashMessage();
    } finally {
        button.disabled = false;
        button.textContent = "Muat Ulang";
    }
}

function switchTab(name) {
    const isLogin = name === "login";
    $("tab-login").classList.toggle("active", isLogin);
    $("tab-register").classList.toggle("active", !isLogin);
    $("form-login").classList.toggle("hidden", !isLogin);
    $("form-register").classList.toggle("hidden", isLogin);
}

// ─── Word Cloud (wordcloud2.js) ───────────────────────────────────────────────

/**
 * Mengambil data frekuensi istilah dari backend lalu merender word cloud
 * pada elemen <canvas> yang diberikan.
 */
async function renderWordCloud(poemId, canvasEl, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "Memuat…";
    try {
        const { status, data } = await api(`wordcloud/${poemId}`);
        if (status === 200 && data.terms && data.terms.length > 0) {
            // wordcloud2.js membutuhkan array [[kata, bobot], ...]
            const maxCount = Math.max(...data.terms.map((t) => t.count));
            const list = data.terms.map((t) => [
                t.term,
                Math.max(8, Math.round((t.count / maxCount) * 48)),
            ]);

            WordCloud(canvasEl, {
                list: list,
                gridSize: 6,
                weightFactor: 1,
                fontFamily: '"Times New Roman", Times, serif',
                color: () => {
                    const palette = [
                        "#1a237e", "#b71c1c", "#004d40", "#e65100",
                        "#4a148c", "#006064", "#880e4f", "#33691e",
                    ];
                    return palette[Math.floor(Math.random() * palette.length)];
                },
                rotateRatio: 0.3,
                backgroundColor: "#f8f8f8",
                drawOutOfBound: false,
            });
            canvasEl.dataset.loaded = "1";
            btnEl.textContent = "☁ Word Cloud";
        } else {
            // Tidak ada data — tampilkan pesan di canvas
            const ctx = canvasEl.getContext("2d");
            ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
            ctx.font = '13px "Times New Roman", Times, serif';
            ctx.fillStyle = "#555";
            ctx.textAlign = "center";
            ctx.fillText(
                "Belum ada data word cloud.",
                canvasEl.width / 2,
                canvasEl.height / 2
            );
            canvasEl.dataset.loaded = "1";
            btnEl.textContent = "☁ Word Cloud";
        }
    } catch (err) {
        btnEl.textContent = "☁ Gagal memuat";
        console.error("renderWordCloud error:", err);
    } finally {
        btnEl.disabled = false;
    }
}

// ─── Event Listeners ──────────────────────────────────────────────────────────
$("tab-login").addEventListener("click", () => switchTab("login"));
$("tab-register").addEventListener("click", () => switchTab("register"));
$("form-login").addEventListener("submit", login);
$("form-register").addEventListener("submit", register);
$("btn-logout").addEventListener("click", logout);
$("form-puisi").addEventListener("submit", submitPuisi);
$("btn-refresh").addEventListener("click", refreshPuisi);

// Trigger preview saat judul, bait, atau template berubah
$("puisi-judul").addEventListener("input", schedulePreview);
$("puisi-bait").addEventListener("input", schedulePreview);
document.querySelectorAll("input[name='template']").forEach((el) => {
    el.addEventListener("change", schedulePreview);
});

checkSession();