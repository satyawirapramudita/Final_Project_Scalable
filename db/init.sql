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
CREATE DATABASE IF NOT EXISTS kbbi_db      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS search_db    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- ── Pengguna per layanan (least privilege) ──
CREATE USER IF NOT EXISTS 'auth_user'@'%'      IDENTIFIED BY 'ganti_auth_password';
CREATE USER IF NOT EXISTS 'poem_user'@'%'      IDENTIFIED BY 'ganti_poem_password';
CREATE USER IF NOT EXISTS 'wordcloud_user'@'%' IDENTIFIED BY 'ganti_wordcloud_password';
CREATE USER IF NOT EXISTS 'kbbi_user'@'%'      IDENTIFIED BY 'ganti_kbbi_password';
CREATE USER IF NOT EXISTS 'search_user'@'%'    IDENTIFIED BY 'ganti_search_password';

GRANT ALL PRIVILEGES ON auth_db.*      TO 'auth_user'@'%';
GRANT ALL PRIVILEGES ON poem_db.*      TO 'poem_user'@'%';
GRANT ALL PRIVILEGES ON wordcloud_db.* TO 'wordcloud_user'@'%';
GRANT ALL PRIVILEGES ON kbbi_db.*      TO 'kbbi_user'@'%';
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

-- ── kbbi_db (Dzaky) ──
USE kbbi_db;
CREATE TABLE IF NOT EXISTS kbbi_entries (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    kata              VARCHAR(100) NOT NULL,
    normalized_kata   VARCHAR(100) NOT NULL,
    bentuk_baku       VARCHAR(100) NULL,
    kelas_kata        VARCHAR(80) NULL,
    dataset_version   VARCHAR(50) NOT NULL,
    UNIQUE KEY uq_kbbi_normalized_version (normalized_kata, dataset_version),
    KEY idx_kbbi_bentuk_baku (bentuk_baku)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS kbbi_definitions (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    entry_id    BIGINT NOT NULL,
    urutan      SMALLINT NOT NULL,
    definisi    TEXT NOT NULL,
    UNIQUE KEY uq_kbbi_definition_order (entry_id, urutan),
    CONSTRAINT fk_kbbi_definition_entry FOREIGN KEY (entry_id)
        REFERENCES kbbi_entries(id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS poetic_terms (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    display_term      VARCHAR(100) NOT NULL,
    normalized_term   VARCHAR(100) NOT NULL UNIQUE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS kbbi_poem_status (
    poem_id          INT PRIMARY KEY,
    status           VARCHAR(20) NOT NULL,
    deleted          TINYINT(1) NOT NULL DEFAULT 0,
    analysis_at      DATETIME(6) NULL,
    source_event_id  CHAR(36) NULL,
    updated_at       DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS poem_terms (
    poem_id      INT NOT NULL,
    term_id      BIGINT NOT NULL,
    analysis_at  DATETIME(6) NOT NULL,
    PRIMARY KEY (poem_id, term_id),
    KEY idx_poem_terms_term (term_id),
    CONSTRAINT fk_poem_terms_term FOREIGN KEY (term_id)
        REFERENCES poetic_terms(id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;
