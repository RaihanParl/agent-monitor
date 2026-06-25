const { test, expect } = require('playwright/test');

test('dashboard shows newest sessions first and agent responses in detail', async ({ page }) => {
  await page.goto('http://127.0.0.1:8787/');
  await page.waitForSelector('.session-item');
  await page.waitForSelector('.chat-timeline .event');

  const bodyText = await page.locator('body').innerText();
  await expect(page).toHaveTitle(/Agent Session Mirror/);
  await expect(page.locator('.app-bar')).toContainText('Agent Session Mirror');
  await expect(page.locator('.session-item').first()).toContainText(/replies|live|ago/);
  await expect(page.locator('.chat-timeline')).toContainText(/Agent|You/);
  await expect(page.locator('body')).toContainText('Newest messages are at the top.');

  const firstCard = await page.locator('.session-item').first().innerText();
  const firstTitles = await page.locator('.chat-timeline .event .event-title').allInnerTexts();

  console.log(JSON.stringify({
    firstCard,
    firstTitles: firstTitles.slice(0, 8),
    bodySnippet: bodyText.slice(0, 2500)
  }, null, 2));
});

test('Telegram filter shows Telegram-sourced Hermes sessions', async ({ page }) => {
  await page.goto('http://127.0.0.1:8787/');
  await page.waitForSelector('.session-item');

  const telegramBtn = page.locator('button.filter[data-filter="Telegram"]');
  await expect(telegramBtn).toBeVisible();
  await expect(telegramBtn).toHaveText('Telegram');

  await telegramBtn.click();
  await page.waitForTimeout(500);

  const body = await page.locator('body').innerText();
  expect(body).toMatch(/Introducing Hermes AI Assistant/);
  expect(body).toMatch(/TELEGRAM|telegram/);
});

test('Hermes sessions show source badge (cli/telegram/tool)', async ({ page }) => {
  await page.goto('http://127.0.0.1:8787/');
  await page.waitForSelector('.session-item');

  const filterBtn = page.locator('button.filter[data-filter="Hermes"]');
  await filterBtn.click();
  await page.waitForTimeout(500);

  const hermesItems = page.locator('.session-item.hermes');
  const count = await hermesItems.count();
  expect(count).toBeGreaterThanOrEqual(1);

  const first = hermesItems.first();
  await expect(first).toContainText(/CLI|TELEGRAM|TOOL|cli|telegram|tool/);
});
