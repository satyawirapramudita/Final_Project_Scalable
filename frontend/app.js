// ─── Konfigurasi ─────────────────────────────────────────────────────────────
// Base URL CloudFront/Lambda untuk image generator.
// Ganti dengan URL distribusi CloudFront Anda, contoh:
//   "https://d1234abcd.cloudfront.net"
// Kosongkan ("") untuk dev lokal — preview akan diganti placeholder dan submit
// tetap bisa dilakukan tanpa gambar (gambar_file = "").
// Di arsitektur microservice gateway tidak meneruskan /fungsi (Lambda Tugas 1), jadi
// generator gambar NONAKTIF secara default agar tidak ada request 404 setiap mengetik.
// Aktifkan dengan mendefinisikan window.PUISI_GENERATOR_URL sebelum app.js dimuat.
const GENERATOR_BASE_URL = window.PUISI_GENERATOR_URL || "";

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
    $("search-results").replaceChildren();
    $("search-pager").classList.add("hidden");
    $("poem-view").classList.add("hidden");
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
        statusEl.textContent = "Generator gambar tidak aktif — puisi tetap bisa dikirim tanpa gambar.";
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

            // Tombol buka tampilan lengkap (KBBI + word cloud besar)
            const openBtn = document.createElement("button");
            openBtn.type = "button";
            openBtn.textContent = "📖 Buka + KBBI";
            openBtn.className = "wc-btn open-btn";
            openBtn.addEventListener("click", (ev) => {
                ev.stopPropagation();
                openPoem(p.id);
            });
            card.appendChild(openBtn);

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
async function renderWordCloud(poemId, canvasEl, btnEl = null) {
    // Tombol opsional: tampilan lengkap (openPoem) memanggil tanpa tombol.
    if (!btnEl) btnEl = document.createElement("button");
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

// ─── Pencarian (search service) ──────────────────────────────────────────────
const SEARCH_LIMIT = 10;
let _search = { q: "", kategori: "", tag: "", page: 1 };

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function startSearch(e) {
    if (e) e.preventDefault();
    _search = {
        q: $("search-q").value.trim(),
        kategori: $("search-kategori").value.trim(),
        tag: "",
        page: 1,
    };
    runSearch();
}

function searchByTag(tag) {
    $("search-q").value = "";
    $("search-kategori").value = "";
    _search = { q: "", kategori: "", tag, page: 1 };
    runSearch();
    $("form-search").scrollIntoView({ behavior: "smooth" });
}

async function runSearch() {
    const msg = $("search-message");
    const results = $("search-results");
    const filter = $("search-filter");
    filter.textContent = _search.tag ? `Tag: #${_search.tag}` : "";

    if (!_search.q && !_search.kategori && !_search.tag) {
        showMessage(msg, "Isi kata kunci atau kategori untuk mencari.", true);
        return;
    }
    showMessage(msg, "Mencari...");
    const qs = new URLSearchParams({ page: _search.page, limit: SEARCH_LIMIT });
    if (_search.q) qs.set("q", _search.q);
    if (_search.kategori) qs.set("kategori", _search.kategori);
    if (_search.tag) qs.set("tag", _search.tag);

    const { status, data } = await api(`search?${qs.toString()}`);
    results.replaceChildren();
    $("search-pager").classList.add("hidden");

    if (status === 401) {
        setToken("");
        showAuth();
        return;
    }
    if (status !== 200 || !data.ok) {
        showMessage(msg, data.message || data.detail || "Pencarian gagal, coba lagi.", true);
        return;
    }
    if (!data.items.length) {
        showMessage(msg, _search.page > 1 ? "Tidak ada hasil lagi." : "Tidak ada puisi yang cocok.");
        return;
    }
    showMessage(msg, `Halaman ${data.page}: ${data.items.length} hasil.`);

    for (const item of data.items) {
        const row = el("div", "search-item");
        row.appendChild(el("strong", "", item.judul));
        if (item.kategori) row.appendChild(el("small", "search-kat", ` — ${item.kategori}`));
        row.appendChild(el("div", "search-snippet", item.snippet || ""));
        if (item.tags && item.tags.length) {
            row.appendChild(el("div", "search-tags", item.tags.map((t) => `#${t}`).join(" ")));
        }
        row.title = "Klik untuk membuka puisi";
        row.addEventListener("click", () => openPoem(item.id));
        results.appendChild(row);
    }

    if (data.page > 1 || data.has_more) {
        $("search-pager").classList.remove("hidden");
        $("btn-search-prev").disabled = data.page <= 1;
        $("btn-search-next").disabled = !data.has_more;
        $("search-page").textContent = ` Halaman ${data.page} `;
    }
}

function changeSearchPage(delta) {
    _search.page = Math.max(1, _search.page + delta);
    runSearch();
}

// ─── Tampilan puisi lengkap (poem + kbbi-linker + wordcloud) ────────────────
let _openPoemId = null;

async function openPoem(poemId) {
    _openPoemId = poemId;
    const view = $("poem-view");
    view.classList.remove("hidden");
    $("view-judul").textContent = "Memuat...";
    $("view-meta").textContent = "";
    $("view-tags").replaceChildren();
    $("view-isi").replaceChildren();
    $("view-kbbi").replaceChildren();
    view.scrollIntoView({ behavior: "smooth" });

    // Tiga layanan dipanggil paralel; masing-masing boleh gagal sendiri.
    const [poemRes, kbbiRes] = await Promise.all([
        api(`poems/${poemId}`),
        api(`kbbi/${poemId}`).catch(() => ({ status: 0, data: {} })),
    ]);
    if (_openPoemId !== poemId) return; // user sudah membuka puisi lain

    if (poemRes.status === 401) {
        setToken("");
        showAuth();
        return;
    }
    if (poemRes.status !== 200 || !poemRes.data.ok) {
        $("view-judul").textContent = poemRes.status === 404 ? "Puisi tidak ditemukan" : "Gagal memuat puisi";
        return;
    }
    const p = poemRes.data.puisi;
    $("view-judul").textContent = p.judul;
    const tgl = p.tgl_submit ? new Date(p.tgl_submit).toLocaleDateString("id-ID") : "-";
    const status = p.status === "done" ? "" : ` · analisis: ${p.status}`;
    $("view-meta").textContent = `${p.kategori || "Tanpa kategori"} · ${tgl}${status}`;

    const tagBox = $("view-tags");
    for (const t of p.tags || []) {
        const chip = el("a", "tag-chip", `#${t}`);
        chip.href = "#";
        chip.title = "Cari puisi lain dengan tag ini";
        chip.addEventListener("click", (ev) => {
            ev.preventDefault();
            searchByTag(t);
        });
        tagBox.appendChild(chip);
    }

    const kbbi = kbbiRes.status === 200 && kbbiRes.data.ok ? kbbiRes.data : null;
    renderIsiWithKbbi($("view-isi"), p.isi || "", kbbi ? kbbi.items : []);
    renderKbbiList($("view-kbbi"), kbbi, kbbiRes.status);
    renderWordCloud(poemId, $("view-wordcloud"));
}

function kbbiTooltip(item) {
    if (!item.found) return `${item.kata_asli}: tidak ditemukan di KBBI`;
    const head = [item.bentuk_baku || item.normalized_term, item.kelas_kata ? `(${item.kelas_kata})` : ""]
        .filter(Boolean)
        .join(" ");
    const defs = (item.definisi || []).slice(0, 3).map((d, i) => `${i + 1}. ${d}`).join("\n");
    return defs ? `${head}\n${defs}` : head;
}

/** Tulis isi puisi dan tandai Kata Puitif dengan tooltip definisi KBBI. */
function renderIsiWithKbbi(container, isi, items) {
    container.replaceChildren();
    const byTerm = new Map();
    for (const item of items || []) byTerm.set(item.normalized_term, item);
    if (!byTerm.size) {
        container.textContent = isi;
        return;
    }
    // Pisah menjadi kata (huruf) dan bukan-kata; spasi & baris baru tetap utuh.
    for (const part of isi.split(/(\p{L}[\p{L}\p{M}'-]*)/u)) {
        const item = part ? byTerm.get(part.toLocaleLowerCase("id-ID")) : null;
        if (item) {
            const span = el("span", item.found ? "kbbi-word" : "kbbi-word kbbi-missing", part);
            span.dataset.tip = kbbiTooltip(item);
            span.tabIndex = 0;
            container.appendChild(span);
        } else if (part) {
            container.appendChild(document.createTextNode(part));
        }
    }
}

function renderKbbiList(container, kbbi, status) {
    container.replaceChildren();
    if (!kbbi) {
        container.appendChild(el("p", "empty", status === 404 ? "Puisi sudah dihapus." : "Layanan KBBI tidak tersedia."));
        return;
    }
    if (kbbi.status === "pending") {
        container.appendChild(el("p", "empty", "Analisis Kata Puitif belum selesai. Coba buka lagi sebentar."));
        return;
    }
    if (!kbbi.items.length) {
        container.appendChild(el("p", "empty", "Tidak ada Kata Puitif pada puisi ini."));
        return;
    }
    const dl = el("dl", "kbbi-list");
    for (const item of kbbi.items) {
        const dt = el("dt", "", item.kata_asli);
        if (item.found && item.bentuk_baku && item.bentuk_baku !== item.normalized_term) {
            dt.appendChild(el("span", "kbbi-baku", ` → ${item.bentuk_baku}`));
        }
        if (item.kelas_kata) dt.appendChild(el("i", "kbbi-kelas", ` (${item.kelas_kata})`));
        dl.appendChild(dt);
        const dd = el("dd");
        if (!item.found) {
            dd.appendChild(el("span", "empty", "Tidak ditemukan di KBBI."));
        } else if (!(item.definisi || []).length) {
            dd.appendChild(el("span", "empty", "Definisi tidak tersedia."));
        } else {
            const ol = el("ol");
            for (const d of item.definisi) ol.appendChild(el("li", "", d));
            dd.appendChild(ol);
        }
        dl.appendChild(dd);
    }
    container.appendChild(dl);
}

function closePoem() {
    _openPoemId = null;
    $("poem-view").classList.add("hidden");
}

// ─── Event Listeners ──────────────────────────────────────────────────────────
$("tab-login").addEventListener("click", () => switchTab("login"));
$("tab-register").addEventListener("click", () => switchTab("register"));
$("form-login").addEventListener("submit", login);
$("form-register").addEventListener("submit", register);
$("btn-logout").addEventListener("click", logout);
$("form-puisi").addEventListener("submit", submitPuisi);
$("btn-refresh").addEventListener("click", refreshPuisi);
$("form-search").addEventListener("submit", startSearch);
$("btn-search-prev").addEventListener("click", () => changeSearchPage(-1));
$("btn-search-next").addEventListener("click", () => changeSearchPage(1));
$("btn-close-view").addEventListener("click", closePoem);

// Trigger preview saat judul, bait, atau template berubah
$("puisi-judul").addEventListener("input", schedulePreview);
$("puisi-bait").addEventListener("input", schedulePreview);
document.querySelectorAll("input[name='template']").forEach((el) => {
    el.addEventListener("change", schedulePreview);
});

checkSession();