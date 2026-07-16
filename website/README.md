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
