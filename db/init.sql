-- ─────────────────────────────────────────────────────────────
-- Inisialisasi MySQL: satu server, database LOGIS terpisah per
-- layanan (kompromi prototipe, lihat dokumen §8).
-- Dijalankan otomatis saat container MySQL pertama kali dibuat.
--
-- CATATAN: password di bawah adalah nilai DEV dan harus SAMA
-- dengan .env / .env.example. Ganti untuk produksi.
-- ─────────────────────────────────────────────────────────────

SET NAMES utf8mb4;

-- ── Database logis ──
CREATE DATABASE IF NOT EXISTS auth_db      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS poem_db      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS wordcloud_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS wiki_db      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS search_db    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- ── Pengguna per layanan (least privilege) ──
CREATE USER IF NOT EXISTS 'auth_user'@'%'      IDENTIFIED BY 'ganti_auth_password';
CREATE USER IF NOT EXISTS 'poem_user'@'%'      IDENTIFIED BY 'ganti_poem_password';
CREATE USER IF NOT EXISTS 'wordcloud_user'@'%' IDENTIFIED BY 'ganti_wordcloud_password';
CREATE USER IF NOT EXISTS 'wiki_user'@'%'      IDENTIFIED BY 'ganti_wiki_password';
CREATE USER IF NOT EXISTS 'search_user'@'%'    IDENTIFIED BY 'ganti_search_password';

GRANT ALL PRIVILEGES ON auth_db.*      TO 'auth_user'@'%';
GRANT ALL PRIVILEGES ON poem_db.*      TO 'poem_user'@'%';
GRANT ALL PRIVILEGES ON wordcloud_db.* TO 'wordcloud_user'@'%';
GRANT ALL PRIVILEGES ON wiki_db.*      TO 'wiki_user'@'%';
GRANT ALL PRIVILEGES ON search_db.*    TO 'search_user'@'%';
FLUSH PRIVILEGES;

-- ── auth_db (Ardi) ──
USE auth_db;
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(50)  NOT NULL UNIQUE,
    password    VARCHAR(255) NOT NULL,          -- PBKDF2: salt:digest
    nama        VARCHAR(100) NOT NULL,
    no_id       VARCHAR(30)  NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- ── poem_db (Satya) ──
USE poem_db;
CREATE TABLE IF NOT EXISTS puisi (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT          NOT NULL,          -- referensi logis ke auth_db.users
    judul       VARCHAR(150) NOT NULL,
    tgl_submit  DATETIME     NOT NULL,
    isi         TEXT         NOT NULL,
    bait        TEXT         NULL,
    kategori    VARCHAR(50)  NOT NULL,
    keyword     VARCHAR(255) NULL,
    gambar_file VARCHAR(255) NULL,
    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending | done
    versi       INT          NOT NULL DEFAULT 1,
    updated_at  DATETIME     NULL
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- Read model tag hasil analisis (diisi dari event poem.analyzed)
CREATE TABLE IF NOT EXISTS puisi_tag (
    puisi_id    INT         NOT NULL,
    tag         VARCHAR(60) NOT NULL,
    PRIMARY KEY (puisi_id, tag)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- ── wordcloud_db (Lindra) ──
USE wordcloud_db;
CREATE TABLE IF NOT EXISTS term_frequency (
    puisi_id    INT          NOT NULL,
    term        VARCHAR(100) NOT NULL,
    count       INT          NOT NULL DEFAULT 0,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (puisi_id, term)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- ── wiki_db (Dzaky) ──
USE wiki_db;
CREATE TABLE IF NOT EXISTS poem_links (
    puisi_id    INT          NOT NULL,
    kata        VARCHAR(100) NOT NULL,          -- Kata Puitif
    wiki_title  VARCHAR(200) NULL,
    wiki_url    VARCHAR(500) NULL,
    found       TINYINT(1)   NOT NULL DEFAULT 0,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (puisi_id, kata)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;
