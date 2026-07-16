import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

test("builds the LumiSync product page as static HTML", async () => {
  const html = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");

  assert.match(html, /<title>LumiSync — Screen\. Sound\. Light\. In sync\.<\/title>/i);
  assert.match(html, /Light that follows/);
  assert.match(html, /No account · No cloud required/);
  assert.match(html, /Different brands\./);
  assert.match(html, /Make room react\./);
  assert.match(html, /LumiSync-Windows-x64-onefile\.exe/);
  assert.match(html, /LumiSync-x86_64\.AppImage/);
  assert.match(html, /https:\/\/lumisync\.minlor\.net\/og\.png/);
  assert.doesNotMatch(html, /codex-preview|chatgpt\.site|react-loading-skeleton/i);
});

test("ships Cloudflare Workers configuration and branded assets", async () => {
  await Promise.all([
    access(new URL("../dist/404.html", import.meta.url)),
    access(new URL("../dist/favicon.svg", import.meta.url)),
    access(new URL("../dist/lumisync-mark.png", import.meta.url)),
    access(new URL("../dist/lumisync-app.png", import.meta.url)),
    access(new URL("../dist/og.png", import.meta.url)),
    access(new URL("../dist/images/devices.png", import.meta.url)),
    access(new URL("../dist/images/monitor-sync.png", import.meta.url)),
    access(new URL("../dist/images/music-sync.png", import.meta.url)),
  ]);

  const [page, config, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /Free &amp; open source · Windows &amp; Linux/);
  assert.match(config, /"pattern": "lumisync\.minlor\.net"/);
  assert.match(config, /"custom_domain": true/);
  assert.match(config, /"directory": "\.\/dist"/);
  assert.match(packageJson, /"deploy": "npm run build && wrangler deploy"/);
  assert.doesNotMatch(packageJson, /vinext|site-creator|react-loading-skeleton/);
});
