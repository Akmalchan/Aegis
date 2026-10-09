const { chromium } = require('playwright-core');
(async () => {
  const fs = require('fs'); const dir = process.env.HOME + '/.cache/ms-playwright/';
  const c = fs.readdirSync(dir).filter(d => d.startsWith('chromium-')).sort().pop();
  const b = await chromium.launch({ executablePath: dir + c + '/chrome-linux64/chrome', args: ['--no-sandbox','--disable-dev-shm-usage'] });
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  const [port, out, list] = process.argv.slice(2);
  for (const i of list.split(',')) {
    await p.goto(`http://localhost:${port}/?still#${i}`, { waitUntil: 'networkidle' });
    await p.reload({ waitUntil: 'networkidle' }); await p.waitForTimeout(3500);
    await p.screenshot({ path: `${out}/aegis-slide${i}.png` });
  }
  await b.close();
})();
