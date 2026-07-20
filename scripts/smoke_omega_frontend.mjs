#!/usr/bin/env node
import { createRequire } from "node:module";
import process from "node:process";

const requireFromFrontend = createRequire(new URL("../frontend/package.json", import.meta.url));
const args = new Set(process.argv.slice(2));
const baseUrlArg = process.argv.slice(2).find((item) => item.startsWith("http://") || item.startsWith("https://"));
const baseUrl = process.env.OMEGA_FRONTEND_URL || baseUrlArg || "http://127.0.0.1:5173";
const strict = process.env.OMEGA_FRONTEND_SMOKE_STRICT === "1";
const screenshotPath = process.env.OMEGA_FRONTEND_SCREENSHOT;

function skip(message) {
  console.warn(`WARNING: ${message}`);
  process.exit(strict ? 1 : 0);
}

function fail(message) {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

function loadPlaywright() {
  for (const name of ["playwright", "@playwright/test"]) {
    try {
      const mod = requireFromFrontend(name);
      if (mod?.chromium) {
        return mod;
      }
    } catch {
      // Try the next package name.
    }
  }
  return null;
}

const playwright = loadPlaywright();
if (args.has("--check-playwright")) {
  process.exit(playwright ? 0 : 2);
}
if (!playwright) {
  skip("Playwright is not installed; skipping Omega frontend smoke test.");
}

async function waitVisible(locator, label, timeout = 10_000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
  } catch (error) {
    throw new Error(`${label} was not visible: ${error.message}`);
  }
}

let browser;
try {
  browser = await playwright.chromium.launch({ headless: true });
} catch (error) {
  skip(`Playwright browser is unavailable; skipping Omega frontend smoke test. ${error.message}`);
}

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  await page.goto(baseUrl, { waitUntil: "networkidle", timeout: 30_000 });

  await waitVisible(page.getByText("Omega Quant Platform"), "Omega header");
  await waitVisible(page.getByText(/LIVE (armed|locked)|LIVE (已武装|未武装)/), "live execution badge");
  await waitVisible(page.getByText(/执行控制中心|Execution control/i), "execution control panel");
  await waitVisible(page.getByText("TigerOpen SDK"), "TigerOpen execution engine");

  await page.getByRole("link", { name: /^(Trading|交易台)$/ }).click();
  await waitVisible(page.getByText(/TigerOpen|RiskManager/), "trading safety surface");

  await page.getByRole("link", { name: /Market \/ Recorder|行情 \/ 录制/ }).click();
  await waitVisible(page.getByText(/行情|自选|录制/), "market and recorder page");

  await page.getByRole("link", { name: /^(Research|研究中心)$/ }).click();
  await waitVisible(page.getByText(/Unified Quant Research|统一量化研究中心/), "research page");

  await page.getByRole("link", { name: /^(Diagnostics|底层诊断)$/ }).click();
  await waitVisible(page.getByText(/底层|引擎|Gateway/), "core diagnostics page");

  if (screenshotPath) {
    await page.getByRole("link", { name: /^(Research|研究中心)$/ }).click();
    await page.waitForTimeout(1_000);
    await page.screenshot({ path: screenshotPath, fullPage: true });
  }

  console.log(`PASS: Omega frontend smoke test passed at ${baseUrl}`);
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
} finally {
  await browser?.close();
}
