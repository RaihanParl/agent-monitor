import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(__dirname, 'docs');
const outFile = path.join(outDir, 'dashboard.png');

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

try {
  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'networkidle' });
  await page.waitForSelector('.app-bar');
  await page.waitForSelector('.session-item');
  await page.waitForSelector('.session-hero');
  await page.waitForSelector('.chat-timeline .event');
  await page.waitForSelector('.runtime-drawer');

  await page.evaluate(() => {
    document.body.classList.add('runtime-open');
    document.body.classList.remove('runtime-closed');
  });

  await page.waitForTimeout(2000);
  await page.screenshot({ path: outFile, fullPage: false });
  console.log(`Saved ${outFile}`);
} finally {
  await browser.close();
}
