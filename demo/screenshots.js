// 重新生成 demo/screenshots/ 下的截图。先在仓库根目录运行
//   python3 -m http.server 8765 -d demo
// 再运行  node demo/screenshots.js
// 使用预装的 Playwright 与 Chromium（PLAYWRIGHT_BROWSERS_PATH），视口 1440×900。
const path = require('path');
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) { ({ chromium } = require('/opt/node22/lib/node_modules/playwright')); }

const BASE = process.env.DEMO_URL || 'http://localhost:8765/';
const OUT = path.join(__dirname, 'screenshots');
const SHOTS = [
  ['#case=A&cfg=ds_flash&step=1', '01-input.png'],
  ['#case=A&cfg=ds_flash&step=2&q=C1', '02-fields.png'],
  ['#case=A&cfg=ds_flash&step=3', '03-conflicts.png'],
  ['#case=A&cfg=ds_flash&step=4', '04-gate.png'],
  ['#case=A&cfg=ds_flash&step=5', '05-cost.png'],
  ['#case=A&cfg=ds_flash&step=6', '06-passport.png'],
  ['#case=C&cfg=glm&step=6', '07-passport-C.png'],
  ['#case=C&cfg=glm&step=4', '08-gate-C.png'],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  for (const [hash, file] of SHOTS) {
    await page.goto(BASE + hash);
    await page.reload();
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, file) });
  }
  await browser.close();
})();
