import Jimp from "jimp";
import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";

const s3 = new S3Client({
  region: "us-east-1",
});

const BUCKET_NAME = "puisi-tim-b-bucket";

// Available poem background templates
const TEMPLATES = {
  "1": "latar1.jpeg",
  "2": "latar2.jpeg",
  "3": "latar3.jpeg",
  "4": "latar5.jpeg",
};

async function streamToBuffer(stream) {
  const chunks = [];
  for await (const chunk of stream) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export const handler = async (event) => {
  try {
    // ----------------------------------------
    // 1. Get request parameters
    // ----------------------------------------
    const params = event.queryStringParameters || {};

    const judul = params.judul || "Judul Puisi";
    const penulis = params.penulis || "Anonim";
    const bait = params.bait || "Lorem ipsum dolor sit amet";
    const template = params.template || "1";

    // "preview" returns the image directly without saving to S3
    const mode = params.mode || "save"; // "save" | "preview"

    const imageKey = TEMPLATES[template];

    if (!imageKey) {
      return {
        statusCode: 400,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          error: "Invalid template",
          availableTemplates: Object.keys(TEMPLATES),
        }),
      };
    }

    // ----------------------------------------
    // 2. Get background image from S3
    // ----------------------------------------
    const s3Res = await s3.send(
      new GetObjectCommand({
        Bucket: BUCKET_NAME,
        Key: imageKey,
      })
    );

    const buffer = await streamToBuffer(s3Res.Body);

    // ----------------------------------------
    // 3. Load image and add poem text (template is upscaled uniformly, aspect ratio preserved)
    // ----------------------------------------
    const image = await Jimp.read(buffer);

    // The template is low resolution, so upscale it uniformly (aspect ratio is
    // preserved, nothing is stretched) and draw the text at the larger size.
    // For a sharper background too, upload higher-resolution templates to S3.
    const TARGET_WIDTH = 1280;
    if (image.bitmap.width < TARGET_WIDTH) {
      image.resize(TARGET_WIDTH, Jimp.AUTO, Jimp.RESIZE_BICUBIC);
    }


    const TEXT_SCALE = 1.1;

    let titleFont = await Jimp.loadFont(Jimp.FONT_SANS_64_BLACK);
    const authorFont = await Jimp.loadFont(Jimp.FONT_SANS_32_BLACK);
    let baitFont = await Jimp.loadFont(Jimp.FONT_SANS_32_BLACK);

    const width = image.bitmap.width;
    const height = image.bitmap.height;

    // Keep text away from the edges (and the arrow in the bottom-right corner)
    const marginX = Math.round(width * 0.1);
    const marginY = 30;
    const usableWidth = width - marginX * 2;

    // Layout is done in layer units (pre-scale)
    const layerWidth = Math.round(usableWidth / TEXT_SCALE);
    const maxLayerHeight = Math.floor((height - marginY * 2) / TEXT_SCALE);
    const penulisText = "by " + penulis;
    const gapTitleAuthor = 10;
    const gapAuthorBait = 20;

    const measure = () => ({
      titleHeight: Jimp.measureTextHeight(titleFont, judul, layerWidth),
      penulisHeight: Jimp.measureTextHeight(authorFont, penulisText, layerWidth),
      baitHeight: Jimp.measureTextHeight(baitFont, bait, layerWidth),
    });
    const blockHeight = (m) =>
      m.titleHeight + gapTitleAuthor + m.penulisHeight + gapAuthorBait + m.baitHeight;

    let m = measure();

    // Too tall? Shrink the bait first, then the title
    if (blockHeight(m) > maxLayerHeight) {
      baitFont = await Jimp.loadFont(Jimp.FONT_SANS_16_BLACK);
      m = measure();
    }
    if (blockHeight(m) > maxLayerHeight) {
      titleFont = await Jimp.loadFont(Jimp.FONT_SANS_32_BLACK);
      m = measure();
    }

    const totalHeight = blockHeight(m);

    // Draw the poem block on a transparent layer
    const layer = new Jimp(layerWidth, totalHeight, 0x00000000);
    let ly = 0;

    layer.print(
      titleFont,
      0,
      ly,
      { text: judul, alignmentX: Jimp.HORIZONTAL_ALIGN_CENTER },
      layerWidth
    );
    ly += m.titleHeight + gapTitleAuthor;

    layer.print(
      authorFont,
      0,
      ly,
      { text: penulisText, alignmentX: Jimp.HORIZONTAL_ALIGN_CENTER },
      layerWidth
    );
    ly += m.penulisHeight + gapAuthorBait;

    layer.print(
      baitFont,
      0,
      ly,
      { text: bait, alignmentX: Jimp.HORIZONTAL_ALIGN_CENTER },
      layerWidth,
      m.baitHeight
    );

    // Scale the text layer up, then center it vertically on the image
    layer.resize(Math.round(layerWidth * TEXT_SCALE), Jimp.AUTO, Jimp.RESIZE_BICUBIC);
    const y = Math.max(marginY, Math.round((height - layer.bitmap.height) / 2));
    image.composite(layer, marginX, y);

    // ----------------------------------------
    // 4. Convert generated image to JPEG
    // ----------------------------------------
    const outputBuffer = await image.getBufferAsync(Jimp.MIME_JPEG);

    // ----------------------------------------
    // 5. PREVIEW MODE
    // ----------------------------------------
    if (mode === "preview") {
      return {
        statusCode: 200,
        headers: {
          "content-type": "image/jpeg",
          "cache-control": "no-cache",
        },
        body: outputBuffer.toString("base64"),
        isBase64Encoded: true,
      };
    }

    // ----------------------------------------
    // 6. SAVE MODE
    // ----------------------------------------
    const timestamp = Date.now();
    const outputKey = `generated/poem-${timestamp}-template${template}.jpeg`;

    await s3.send(
      new PutObjectCommand({
        Bucket: BUCKET_NAME,
        Key: outputKey,
        Body: outputBuffer,
        ContentType: "image/jpeg",
      })
    );

    // ----------------------------------------
    // 7. Return result information
    // ----------------------------------------
    return {
      statusCode: 200,
      headers: {
        "content-type": "application/json",
        "cache-control": "no-cache",
      },
      body: JSON.stringify({
        success: true,
        template: template,
        background: imageKey,
        judul: judul,
        penulis: penulis,
        bait: bait,
        key: outputKey,
        bucket: BUCKET_NAME,
      }),
    };
  } catch (err) {
    console.error("Processing error:", err);
    return {
      statusCode: 500,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        success: false,
        error: err.message,
      }),
    };
  }
};
