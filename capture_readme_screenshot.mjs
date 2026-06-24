import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(__dirname, 'docs');
const outFile = path.join(outDir, 'dashboard.png');

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'networkidle' });
  await page.waitForSelector('.session-item');
  await page.waitForSelector('.app-bar');
  await page.waitForTimeout(1500);
  await page.screenshot({ path: outFile, fullPage: false });
  console.log(`Saved ${outFile}`);
} finally {
  await browser.close();
}
