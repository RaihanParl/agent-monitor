import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });

try {
  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'networkidle' });
  await page.waitForSelector('.session-item');
  await page.waitForSelector('.session-hero');

  const data = await page.evaluate(() => {
    const sessionItems = Array.from(document.querySelectorAll('.session-item'));
    const eventNodes = Array.from(document.querySelectorAll('.timeline .event'));
    const statusBadges = Array.from(document.querySelectorAll('.status-badge'));
    const firstSessionText = sessionItems[0]?.innerText || '';
    const firstSessionUpdated = firstSessionText.match(/(\d+[smhd]\s+ago|live)/)?.[1] || '';
    const eventTitles = eventNodes.slice(0, 6).map(el => el.querySelector('.event-title')?.textContent?.trim() || '');
    const eventBodies = eventNodes.slice(0, 6).map(el => el.querySelector('.event-preview')?.textContent?.trim() || '');
    const assistantVisible = eventTitles.some(t => /Agent/i.test(t)) || eventBodies.some(t => t.length > 40);
    const newestHintVisible = document.body.innerText.includes('Newest messages are at the top.');
    const markdownRendered = !!document.querySelector('.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4, .markdown-body code, .markdown-body strong');
    const darkShellVisible = !!document.querySelector('.app-bar .brand-mark') && getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)';
    const counts = Array.from(document.querySelectorAll('.summary-chip strong')).map(el => el.textContent?.trim() || '');
    const runtimeStatusVisible = statusBadges.some(el => /running|idle/i.test(el.textContent || ''));
    const topbarRunningVisible = document.getElementById('topbar')?.innerText?.includes('running') || false;
    return {
      title: document.title,
      sessionCount: sessionItems.length,
      firstSessionUpdated,
      firstSessionText,
      eventTitles,
      eventBodies,
      assistantVisible,
      newestHintVisible,
      markdownRendered,
      darkShellVisible,
      runtimeStatusVisible,
      topbarRunningVisible,
      statusBadgeCount: statusBadges.length,
      counts,
      bodySnippet: document.body.innerText.slice(0, 2500),
    };
  });

  console.log(JSON.stringify(data, null, 2));
} finally {
  await browser.close();
}
