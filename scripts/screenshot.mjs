#!/usr/bin/env node
/**
 * Capture compressed JPEG screenshots from a local HTML file.
 * Usage: node scripts/screenshot.mjs '{"htmlPath":"/path/index.html","outDir":"/path/shots"}'
 */
import fs from "fs";
import path from "path";
import { chromium } from "playwright";

function fail(message, code = 1) {
  process.stderr.write(String(message) + "\n");
  process.exit(code);
}

function parsePayload() {
  const raw = process.argv[2];
  if (!raw) fail("missing payload");
  try {
    return JSON.parse(raw);
  } catch (e) {
    fail("invalid JSON: " + e.message);
  }
}

async function shot(page, filePath, width, height, fullPage = false) {
  await page.setViewportSize({ width, height });
  await page.goto(`file://${filePath}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(500);
  await page.screenshot({
    path: filePath.replace(/index\.html$/, "").endsWith("/")
      ? filePath
      : undefined,
    ...(fullPage ? { fullPage: true } : { clip: { x: 0, y: 0, width, height: Math.min(height, 900) } }),
  });
}

async function main() {
  const payload = parsePayload();
  const htmlPath = path.resolve(payload.htmlPath || "");
  const outDir = path.resolve(payload.outDir || "");
  if (!fs.existsSync(htmlPath)) fail("html file not found: " + htmlPath);
  fs.mkdirSync(outDir, { recursive: true });

  const fileUrl = "file://" + htmlPath;
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const outputs = [];

  // Hero desktop (top viewport)
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(fileUrl, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(600);
  const heroPath = path.join(outDir, "hero.jpg");
  await page.screenshot({
    path: heroPath,
    type: "jpeg",
    quality: 80,
    clip: { x: 0, y: 0, width: 1280, height: 720 },
  });
  outputs.push({ name: "hero", path: heroPath, label: "Startsida" });

  // Full desktop scroll capture (compressed)
  const fullPath = path.join(outDir, "desktop.jpg");
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(fileUrl, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(400);
  await page.screenshot({
    path: fullPath,
    type: "jpeg",
    quality: 75,
    fullPage: true,
  });
  outputs.push({ name: "desktop", path: fullPath, label: "Desktop" });

  // Mobile
  const mobilePath = path.join(outDir, "mobile.jpg");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(fileUrl, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(400);
  await page.screenshot({
    path: mobilePath,
    type: "jpeg",
    quality: 80,
    fullPage: false,
    clip: { x: 0, y: 0, width: 390, height: 700 },
  });
  outputs.push({ name: "mobile", path: mobilePath, label: "Mobil" });

  await browser.close();
  process.stdout.write(JSON.stringify({ ok: true, shots: outputs }));
}

main().catch((err) => fail(err?.message || String(err), 2));
