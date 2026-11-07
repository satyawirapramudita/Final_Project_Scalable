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

async function api(action, method = "GET", body = null) {
    const options = { method, headers: {} };
    if (body !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
    }
    // Pakai prefix /api agar via CloudFront routing ke origin EC2 (behavior /api*).
    // Server Python mengabaikan path dan hanya baca query ?action=, jadi
    // /api/?action=... tetap works untuk dev lokal (localhost:8000) maupun EC2 langsung.
    const res = await fetch(`/api?action=${action}`, options);
    const data = await res.json().catch(() => ({}));
    return { status: res.status, data };
}

// ─── Sesi ────────────────────────────────────────────────────────────────────
async function checkSession() {
    const { data } = await api("me");
    if (data.ok) {
        enterApp(data.nama, data.username);
    } else {
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
    const { status, data } = await api("login", "POST", {
        username: $("login-username").value,
        password: $("login-password").value,
    });
    if (status === 200 && data.ok) {
        $("form-login").reset();
        enterApp(data.nama, data.username);
    } else {
        showMessage($("auth-message"), data.message || "Login gagal", true);
    }
}

async function register(e) {
    e.preventDefault();
    showMessage($("auth-message"), "");
    const { status, data } = await api("register", "POST", {
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
    await api("logout", "POST");
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
        const { status, data } = await api("submit_puisi", "POST", {
            judul: $("puisi-judul").value.trim(),
            kategori: $("puisi-kategori").value.trim(),
            keyword: $("puisi-keyword").value.trim(),
            isi: $("puisi-isi").value.trim(),
            bait: bait,
            template: params.template,
            gambar_file: gambar_file,
        });

        if (status === 201 && data.ok) {
            $("form-puisi").reset();
            $("preview-gambar").style.display = "none";
            $("preview-status").textContent = "";
            showMessage($("puisi-message"), data.message, false);
            loadPuisi();
        } else {
            showMessage($("puisi-message"), data.message || "Gagal mengirim puisi", true);
            if (status === 401) showAuth();
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.value = "Kirim Puisi";
    }
}

// ─── Daftar Puisi (Grid Kartu) ────────────────────────────────────────────────
async function loadPuisi() {
    const { status, data } = await api("daftar_puisi");
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

            card.style.cursor = "pointer";
            card.title = "Klik untuk lihat / sembunyikan isi";
            card.addEventListener("click", () => {
                detail.classList.toggle("hidden");
            });

            grid.appendChild(card);
        }
        return true;
    } else if (status === 401) {
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