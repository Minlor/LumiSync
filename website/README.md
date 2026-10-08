# LumiSync website

The product and download site for [LumiSync](https://github.com/Minlor/LumiSync). It is generated as static HTML by Astro and deployed to the existing Cloudflare Pages project named `lumisync`.

## Development

```bash
npm install
npm run dev
```

## Deploy to Cloudflare Pages

Authenticate once with `npx wrangler login`, then run:

```bash
npm run deploy
```

This command publishes the production `main` deployment. The Pages project
already owns `lumisync.minlor.net`; its custom domain and DNS are managed in
Cloudflare Pages. `wrangler.jsonc` declares the existing project and Astro's
`dist` output, with the project's downloaded compatibility date. Do not deploy
this site as a separate Worker with the same custom domain.

For a preview, build and use a different branch explicitly:

```bash
npm run build
npx wrangler pages deploy dist --project-name=lumisync --branch=preview-name
```

This repository has no website deployment workflow in GitHub Actions. Publish
the validated site with the command above; the Pages project's Git integration
is managed separately in Cloudflare. A future GitHub Actions
workflow would need `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` repository
secrets and should run `npm test` before deploying.

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

The 0.8.0 browser checks and design delivery gate are recorded in
[the website verification report](../docs/website-ui-verification.md).
