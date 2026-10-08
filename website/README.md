# LumiSync website

The product and download site for [LumiSync](https://github.com/Minlor/LumiSync). It is generated as static HTML by Astro and deployed directly to Cloudflare Workers Static Assets.

## Development

```bash
npm install
npm run dev
```

## Deploy to Cloudflare Workers

Authenticate once with `npx wrangler login`, then run:

```bash
npm run deploy
```

`wrangler.jsonc` declares `lumisync.minlor.net` as a Worker Custom Domain. Cloudflare creates the DNS record and certificate during deployment; `minlor.net` must be an active zone in the authenticated Cloudflare account.

For automatic deployments, add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as GitHub Actions repository secrets. The workflow validates the static build before deploying it.

Download buttons use GitHub's stable `releases/latest/download/` URLs, so releases do not require website updates while artifact names remain unchanged.

The 0.8.0 page documents personal vendor accounts, device capability controls,
supported plug metering and local-sync limitations. Screenshots show the real
application with labelled example data. Regenerate them from the repository root:

```bash
python tools/capture_example_screenshots.py
```

Copy the devices, monitor-sync, music-sync, accounts and plug-energy captures
from `docs/images/lumisync-*.png` to the corresponding files in `public/images/`.
Run `npm test` before deploying, and publish the matching app release before
updating the public website's version and release-notes link.
