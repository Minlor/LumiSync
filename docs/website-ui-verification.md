# LumiSync 0.8.0 website verification

Status: **PASS**, 8 October 2026. The product site is published at
[lumisync.minlor.net](https://lumisync.minlor.net/). This report covers the
static website; the desktop interaction report is
[account-device-ui-verification.md](account-device-ui-verification.md).
The design direction was recorded before implementation in
[account-device-ui-design.md](account-device-ui-design.md).

## Executed checks

- `npm --prefix website test`: both production HTML/assets checks pass.
- The actual browser was checked at desktop, 768px and 375px widths. The
  published page has no horizontal document overflow at either smaller width.
  The account screenshot loads at 860x700 and fits a 320px content width on the
  phone layout. The normal browser viewport was restored afterward.
- Features, Devices, Download and Home navigation were exercised. Tab gives
  Devices a visible 2px blue focus outline, and Enter navigates to its section.
  Tab from the account guide reaches the account screenshot and loads it.
- All five distinct screenshot links were opened on the local production build.
  The public account screenshot was also opened and returns the original
  860x700 image. Public inventory, plug, music and monitor captures load with
  their new 1297px source width. Every product capture has an Example data
  caption and a link to its full-size image.
- Public Account setup and Release notes links were clicked. They open the
  merged account guide and the published LumiSync 0.8.0 release.
- All three stable binary download URLs return HTTP 200 and their expected
  release assets. The five public release files were downloaded and their
  SHA-256 hashes match GitHub's asset digests. PyPI reports version 0.8.0 and
  both Python distributions.
- Computed foreground/background contrast for 78 rendered text elements has
  no AA failures; the minimum measured ratio is 7.32:1 on the solid surfaces.
- The release pipelines passed: [Windows](https://github.com/Minlor/LumiSync/actions/runs/37802269283),
  [Linux](https://github.com/Minlor/LumiSync/actions/runs/37802268995) and
  [PyPI](https://github.com/Minlor/LumiSync/actions/runs/37802269045).
  The Windows job runs 424 tests and starts both packaged variants with
  `--help`, `--check-integrations` and `--check-gui`. Linux starts the actual
  AppImage on a clean Ubuntu 22.04 X11 runtime.

## Deployment correction

The public domain belongs to the existing Cloudflare Pages project `lumisync`.
The old repository command targeted a separate Worker, so its custom-domain
registration conflicted and the public site remained unchanged. The existing
Pages configuration was downloaded and checked; it has no service bindings and
uses compatibility date 2026-07-16. The repository now uses that project and
`pages_build_output_dir`, and `npm run deploy` publishes its production `main`
deployment. Deployment 525d9a77 completed successfully, and the custom domain
visibly serves the new page and screenshots. The unused Worker created by the
failed attempt was removed; the existing Pages project and domain remain active.
There is no website deployment workflow in this repository's GitHub Actions.

## Antislop delivery gate

Each rule below passes for the implemented website scope. Existing brand
assets and unchanged title text are retained; examples are deliberately
identified rather than presented as live user or device data.

| Gate | Result and concrete evidence |
| --- | --- |
| R-01 | PASS: blue identifies downloads, links and focus; neutral surfaces support product captures. |
| R-02 | PASS: newly authored page copy has no em dashes; the existing document title is unchanged. |
| R-03 | PASS: desktop, tablet and phone layouts were rendered; no horizontal document overflow. |
| R-04 | PASS: Windows/Linux and task glyphs identify their actual platforms or features. |
| R-05 | PASS: hero, feature summary, product captures, comparison table and downloads serve different tasks. |
| R-06 | PASS: existing system typography is retained; heading/body/caption sizes separate hierarchy. |
| R-07 | PASS: solid backgrounds contain no decorative patterns. |
| R-08 | PASS: download glyphs describe file actions; no extra decorative CTA arrows were introduced. |
| R-09 | PASS: redundant eyebrow badges removed; example captions describe the captures. |
| R-10 | PASS: solid website surfaces; no decorative glass panels. |
| R-11 | PASS: established corner sizes remain consistent across buttons and screenshots. |
| R-12 | PASS: repeated shadows removed; spacing, headings and borders establish hierarchy. |
| R-13 | PASS: no decorative glows; blue outlines identify keyboard focus. |
| R-14 | PASS: captures represent real app views; the family comparison uses a native table. |
| R-15 | PASS: Download, Read account setup and Release notes name their destinations. |
| R-16 | PASS: copy describes controls, account setup, measured quantities and connection limits. |
| R-17 | PASS: displayed watts and kWh are clearly labelled example data from the approved fixtures. |
| R-18 | PASS: no testimonials, fictional users or invented social proof. |
| R-19 | PASS: brief hover/focus and section-navigation feedback; no looping decoration. |
| R-20 | PASS: actual account, device and plug screenshots identify LumiSync's current functionality. |
| R-21 | PASS: existing charcoal identity retained; no incomplete theme control advertised. |
| R-22 | PASS: screenshots are actual rendered widgets; no illustrative substitute for product UI. |
| R-23 | PASS: existing brand assets and user-authorized example captures are used. |
| R-24 | PASS: navigation anchors, account guide and release links reach their real destinations. |
| R-25 | PASS: 78 rendered text elements pass computed contrast; minimum 7.32:1. |
| R-26 | PASS: screenshot links open originals; binary URLs return their actual public release files. |
| R-27 | PASS: unsupported models/connections and missing measurements have concrete setup/availability guidance. |
| R-28 | PASS: no FAQ or generic filler; setup links support the described account task. |
| R-29 | PASS: existing charcoal neutrals and blue accent retained; screenshot colors are product data. |
| R-30 | PASS: LumiSync branding and actual product screenshots; no borrowed product identity. |
| R-31 | PASS: layout, type, color, icon and motion purposes are in the linked design rationale. |
| R-32 | PASS: keyboard Tab focus and Enter navigation exercised; screenshot links have accessible names. |
| R-33 | PASS: production source edited directly; capture tooling renders the real desktop widgets. |
| R-34 | PASS: responsive website behavior verified; no alternate website theme is declared. |
| R-35 | PASS: production build/tests, browser interactions, public deployment and downloads verified. |
| R-36 | PASS: cloud manual-control/local-sync distinction and firmware/meter availability limits stated. |
| R-37 | PASS: existing identity and desktop lighting-control purpose declared before redesign. |
| R-38 | PASS: every product capture says Example data; no fabricated public metrics. |

| Craftsmanship gate | Result and evidence |
| --- | --- |
| C-1 | PASS: written rationale ties hierarchy, captures, type and controls to product understanding. |
| C-2 | PASS: new links reach the public guide, release, original images and binary downloads. |
| C-3 | PASS: every section supports understanding, setup, compatibility or downloading LumiSync. |
| C-4 | PASS: desktop/tablet/phone and keyboard behavior checked; examples remain legible full-size. |
| C-5 | PASS: sample data and model/firmware limits are explicit; tests are distinguished from physical validation. |

| UI checklist | Result and evidence |
| --- | --- |
| UI-01 | PASS: palette follows the existing charcoal/blue identity. |
| UI-02 | PASS: accent identifies action, links and focus. |
| UI-03 | PASS: no decorative emoji in headings, buttons or lists. |
| UI-04 | PASS: task-specific layouts follow RHYTHM 2. |
| UI-05 | PASS: no bento, fake terminal, pricing template or meaningless decorative stripe. |
| UI-06 | PASS: no repeated badge above headings. |
| UI-07 | PASS: navigation, setup/release links, image originals and download targets verified. |
| UI-08 | PASS: brief functional transitions match MOTION 2. |
| UI-09 | PASS: solid surfaces and established corner hierarchy; no repeated glow/shadow stack. |
| UI-10 | PASS: product captures show labelled fixture device states; no fake live website indicators. |
| UI-11 | PASS: sections focus on product capability, setup or obtaining the app. |
| UI-12 | PASS: approved example readings have explicit captions; no public adoption metrics invented. |
| UI-13 | PASS: website requests no credentials; captures have empty password inputs and masked example accounts. |
| UI-14 | PASS: connection and availability limits point to the actual setup guide. |
| UI-15 | PASS: responsive layouts, keyboard focus and measured text contrast pass. |

**Liveliness PASS:** ENERGY 1 / RHYTHM 2 / MOTION 2 match the written direction.
Downloads and the real device view lead the hero. Whitespace groups related
decisions, the blue accent identifies action/focus, and the existing charcoal
workspace motif connects the site to the desktop app.

The browser proof is saved locally as
`build/vendor-audit/release-0.8.0-website-live.jpg`. Desktop fixture screenshots
are committed under `docs/images/`. Browser checks do not establish physical
bulb/plug compatibility or native DWM rendering on every Windows system.
