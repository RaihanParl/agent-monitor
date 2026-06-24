const { test, expect } = require('playwright/test');

test('dashboard shows newest sessions first and agent responses in detail', async ({ page }) => {
  await page.goto('http://127.0.0.1:8787/');
  await page.waitForSelector('.session-item');
  await page.waitForSelector('.timeline .event');

  const bodyText = await page.locator('body').innerText();
  await expect(page).toHaveTitle(/Agent Session Mirror/);
  await expect(page.locator('.app-bar')).toContainText('Agent Session Mirror');
  await expect(page.locator('.session-item').first()).toContainText(/replies|live|ago/);
  await expect(page.locator('.timeline')).toContainText(/Agent|You/);
  await expect(page.locator('body')).toContainText('Newest messages are at the top.');

  const firstCard = await page.locator('.session-item').first().innerText();
  const firstTitles = await page.locator('.timeline .event .event-title').allInnerTexts();

  console.log(JSON.stringify({
    firstCard,
    firstTitles: firstTitles.slice(0, 8),
    bodySnippet: bodyText.slice(0, 2500)
  }, null, 2));
});
