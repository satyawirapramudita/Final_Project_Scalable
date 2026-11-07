/**
 * generate-puisi-image/index.mjs
 *
 * Lambda Function URL handler.
 * Merender kutipan puisi (judul, penulis, bait) di atas gambar template statis dari S3.
 *
 * Query params yang diterima:
 *   template  - nama file template di S3 (contoh: template1.jpg). Wajib.
 *   judul     - judul puisi. Wajib.
 *   penulis   - nama penulis. Wajib.
 *   bait      - kutipan bait pendek. Wajib.
 *   save      - "true" → upload hasil ke S3 & return JSON filename
 *               absen/"false" → return JPEG sebagai base64 (untuk preview)
 *
 * Env vars yang harus dikonfigurasi di Lambda:
 *   S3_BUCKET   - nama bucket S3 (template dan hasil disimpan di bucket yang sama)
 *   S3_REGION   - region bucket, contoh: ap-southeast-1
 */

import Jimp from "jimp";
import { S3Client, GetObjectCommand, PutObjectCommand } from "@aws-sdk/client-s3";
import { randomBytes } from "crypto";

const S3_BUCKET = process.env.S3_BUCKET;
const S3_REGION = process.env.S3_REGION || "ap-southeast-1";
const HASIL_PREFIX = "hasil-puisi/";

const s3 = new S3Client({ region: S3_REGION });

// ── Jimp bitmapped font tersedia lewat Jimp.loadFont() tanpa dependency sistem ──
// Gunakan font bawaan Jimp (Roboto/sans bitmap 32/16 px sesuai mode)
const FONT_BIG   = Jimp.FONT_SANS_32_WHITE; // judul
const FONT_MED   = Jimp.FONT_SANS_16_WHITE; // "by penulis"
const FONT_SMALL = Jimp.FONT_SANS_16_WHITE; // bait (baris-baris)

/** Stream S3 → Buffer */
async function s3ToBuffer(key) {
  const cmd = new GetObjectCommand({ Bucket: S3_BUCKET, Key: key });
  const res = await s3.send(cmd);
  const chunks = [];
  for await (const chunk of res.Body) chunks.push(chunk);
  return Buffer.concat(chunks);
}

/** Bungkus teks panjang menjadi beberapa baris agar muat di lebar tertentu */
function wrapText(text, font, maxWidth, jimp) {
  const words = text.split(" ");
  const lines = [];
  let current = "";
  for (const word of words) {
    const test = current ? `${current} ${word}` : word;
    if (Jimp.measureText(font, test) <= maxWidth) {
      current = test;
    } else {
      if (current) lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/** Render teks dengan semi-transparant overlay gelap di area teks agar terbaca */
async function renderImage({ templateBuffer, judul, penulis, bait }) {
  const img = await Jimp.read(templateBuffer);
  const W = img.getWidth();
  const H = img.getHeight();

  // Overlay gelap semi-transparan (kotak hitam 55% opacity) di 2/3 bawah gambar
  const overlay = new Jimp(W, Math.floor(H * 0.68), 0x00000080); // RGBA: hitam 50%
  img.composite(overlay, 0, Math.floor(H * 0.32));

  const fontBig   = await Jimp.loadFont(FONT_BIG);
  const fontMed   = await Jimp.loadFont(FONT_MED);
  const fontSmall = await Jimp.loadFont(FONT_SMALL);

  const margin = 32;
  const textWidth = W - margin * 2;

  // ── Judul (besar, 40% dari atas) ──
  const judulLines = wrapText(judul.toUpperCase(), fontBig, textWidth);
  let y = Math.floor(H * 0.34);
  for (const line of judulLines) {
    const lw = Jimp.measureText(fontBig, line);
    img.print(fontBig, Math.floor((W - lw) / 2), y, line);
    y += Jimp.measureTextHeight(fontBig, line, textWidth) + 4;
  }

  // ── Penulis (medium, satu baris) ──
  y += 8;
  const penulisTxt = `— ${penulis}`;
  const pw = Jimp.measureText(fontMed, penulisTxt);
  img.print(fontMed, Math.floor((W - pw) / 2), y, penulisTxt);
  y += Jimp.measureTextHeight(fontMed, penulisTxt, textWidth) + 16;

  // ── Bait (kecil, rata tengah, italic-style tidak tersedia di Jimp bitmap;
  //    untuk memberi kesan "kutipan" tambahkan tanda petik) ──
  const baitFormatted = `"${bait}"`;
  const baitLines = wrapText(baitFormatted, fontSmall, textWidth);
  for (const line of baitLines) {
    const lw = Jimp.measureText(fontSmall, line);
    img.print(fontSmall, Math.floor((W - lw) / 2), y, line);
    y += Jimp.measureTextHeight(fontSmall, line, textWidth) + 2;
    if (y > H - margin) break; // jangan meluber ke bawah
  }

  return img.getBufferAsync(Jimp.MIME_JPEG);
}

// ── Sanitasi ringan: tolak path traversal pada nama template ──
function safeTemplateName(name) {
  // Hanya izinkan karakter alfanumerik, titik, strip — tanpa path separator
  return /^[\w.-]{1,80}$/.test(name) ? name : null;
}

export const handler = async (event) => {
  // Lambda Function URL meneruskan query string ke event.queryStringParameters
  const qs = event.queryStringParameters || {};

  const templateRaw = (qs.template || "").trim();
  const judul       = (qs.judul   || "").trim().slice(0, 150);
  const penulis     = (qs.penulis || "").trim().slice(0, 100);
  const bait        = (qs.bait    || "").trim().slice(0, 500);
  const saveMode    = qs.save === "true";

  // ── Validasi input ──
  if (!templateRaw || !judul || !penulis || !bait) {
    return {
      statusCode: 400,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok: false, message: "Parameter template, judul, penulis, bait wajib diisi" }),
    };
  }
  const templateKey = safeTemplateName(templateRaw);
  if (!templateKey) {
    return {
      statusCode: 400,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok: false, message: "Nama template tidak valid" }),
    };
  }

  let templateBuffer;
  try {
    templateBuffer = await s3ToBuffer(templateKey);
  } catch (err) {
    console.error("Gagal mengambil template dari S3:", err);
    return {
      statusCode: 404,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok: false, message: `Template '${templateKey}' tidak ditemukan di S3` }),
    };
  }

  let jpegBuffer;
  try {
    jpegBuffer = await renderImage({ templateBuffer, judul, penulis, bait });
  } catch (err) {
    console.error("Gagal render gambar:", err);
    return {
      statusCode: 500,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok: false, message: "Gagal merender gambar" }),
    };
  }

  // ── Mode save=true: upload ke S3, return JSON filename ──
  if (saveMode) {
    const rand = randomBytes(6).toString("hex");
    const filename = `${HASIL_PREFIX}puisi-${Date.now()}-${rand}.jpg`;
    try {
      await s3.send(new PutObjectCommand({
        Bucket: S3_BUCKET,
        Key: filename,
        Body: jpegBuffer,
        ContentType: "image/jpeg",
        // Akses publik dikendalikan dari bucket policy / CloudFront OAC, bukan dari sini
      }));
    } catch (err) {
      console.error("Gagal upload ke S3:", err);
      return {
        statusCode: 500,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ok: false, message: "Gagal menyimpan gambar ke S3" }),
      };
    }
    return {
      statusCode: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      body: JSON.stringify({ ok: true, filename }),
    };
  }

  // ── Mode preview (default): return JPEG sebagai base64 ──
  return {
    statusCode: 200,
    headers: {
      "Content-Type": "image/jpeg",
      // Izinkan cache singkat di browser/CloudFront untuk preview dengan parameter yang sama
      "Cache-Control": "public, max-age=60",
    },
    body: jpegBuffer.toString("base64"),
    isBase64Encoded: true,
  };
};
