#!/usr/bin/env node
import { createRequire } from "node:module";
import { writeFile } from "node:fs/promises";
import process from "node:process";

const requireFromFrontend = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium } = requireFromFrontend("@playwright/test");
const { GIFEncoder, quantize, applyPalette } = requireFromFrontend("gifenc");
const { PNG } = requireFromFrontend("pngjs");

const baseUrl = process.argv[2] || "http://127.0.0.1:8770";
const output = process.argv[3] || "docs/assets/omega-demo.gif";
const width = 1120;
const height = 720;
const frames = [];

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByRole("link", { name: /^(Research|研究中心)$/ }).click();
  await page.waitForTimeout(1_000);
  frames.push(await page.screenshot({ type: "png" }));
  await page.getByRole("button", { name: "中文" }).click();
  await page.waitForTimeout(500);
  frames.push(await page.screenshot({ type: "png" }));
  await page.getByRole("button", { name: "EN" }).click();
  await page.waitForTimeout(500);
  frames.push(await page.screenshot({ type: "png" }));
} finally {
  await browser.close();
}

const gif = GIFEncoder();
for (const [index, encoded] of frames.entries()) {
  const image = PNG.sync.read(encoded);
  const palette = quantize(image.data, 128, { format: "rgba4444" });
  const pixels = applyPalette(image.data, palette, "rgba4444");
  gif.writeFrame(pixels, width, height, {
    palette,
    delay: index === frames.length - 1 ? 2200 : 1400,
    repeat: 0,
  });
}
gif.finish();
await writeFile(output, gif.bytes());
console.log(`Wrote ${frames.length}-frame Omega demo GIF to ${output}`);
