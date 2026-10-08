# Verification record

8 October 2026. This record consolidates the previous exploratory audits and UI
reports. [Account setup](vendor-accounts.md) and [integrations](integrations.md)
describe current behavior; old plans remain available in Git history.

## 0.8.1 account and stability checks

| Executed check | Result |
| --- | --- |
| Full source suite on Windows, Qt 6.11.1 | PASS: 429 tests |
| Full source suite on Windows, Qt 6.12.0 | PASS: 429 tests |
| Ruff for `lumisync` and `tests` | PASS |
| Signed Tuya and LSC service checks in EU, US, CN and IN | PASS: eight checks |
| Fresh RSA challenge for both providers with no package access | PASS |
| Password protocol, RSA encoding, verification, expiry and override fixtures | PASS |
| Credentials and vendor response text omitted from sign-in diagnostics | PASS |
| Fresh real Tuya email/password sign-in using built-in configuration | PASS: user confirmed; one supported device imported |
| Fresh real LSC email/password sign-in using built-in configuration | PASS: user confirmed; three supported devices imported |
| Fresh real Govee sign-in, including email verification | PASS: one supported device imported |
| Six isolated rapid-control runs on Qt 6.12.0 | PASS: 1,200 interactions per run; bounded queue and final state checked |
| Local frozen Windows test app: help, integration dependencies and GUI startup | PASS |

The user entered credentials directly in the application. Local diagnostics
independently recorded successful Tuya/LSC imports with `configuration=built-in`.
The sign-in log records provider, configuration source, safe result code and
supported-device count. It omits emails, passwords, verification codes, app keys
and sessions. The local frozen test build exercised the final account protocol;
it retained its pre-release version label during that test.

A Qt 6.12.0 rapid-control probe reproduced a native GUI stall during repeated
style invalidation. Device cards and inspectors now rebuild power/status styles
only when those states change. The subprocess regression uses a native timeout
watchdog and reports progress rather than relying on a blocked GUI timer.
The earlier native worker-lifetime fix waits for `QThread.wait(0)` before
releasing worker ownership. These checks reduce the reproduced failures; they
do not establish that every native driver or firmware is fault free.

Local logs and probes are kept under ignored `build/vendor-audit/`. They are
not release downloads. CI and packaged release runs are available in
[GitHub Actions](https://github.com/Minlor/LumiSync/actions).

## Interface direction and coverage

The design uses LumiSync's existing charcoal surfaces, blue action/focus accent
and system UI sans-serif. ENERGY 1 / RHYTHM 2 / MOTION 2 means quiet workspaces,
task-specific layouts and short transitions that explain state/navigation.
Power, brightness and measured values lead device controls. Advanced details
expand on demand. Device icons also have text labels. The page heading and
toolbar stay above both the inventory and inspector so opening a device does
not move basic actions. Inspector height follows its content.

The account form prioritizes email/password and editable country selection;
alternatives and optional configuration appear only when relevant. Short/narrow
layouts keep primary actions visible. Monitor/Music Start/Stop remain pinned;
Draw gives the canvas most space; Settings changes to a section selector when
needed. The website leads with downloads and actual product captures, followed
by capabilities, connection limits and account setup. Solid surfaces and
spacing establish hierarchy without decorative glow or repeated shadows.

The desktop layout/interaction checks cover Accounts, Devices, meters,
Monitor Sync, Music Sync, Draw, Settings, Add Device, Panel tools, notifications
and navigation. Actual Qt fixtures were rendered at 800×520, 1120×760,
1120×792, 1143×792, 1297×792 and 1600×900. The six-device inventory fits without
scrolling for basic controls at the four larger inventory sizes. Opening an
inspector preserves heading/action geometry. Account forms were checked down
to 480×440 with submission and Close visible. Meter refresh, retained offline
readings, daily data, keyboard actions and responsive states have regression
coverage in `tests/test_account_device_ui.py` and the UI test modules.

Fresh 0.8.1 product screenshots were captured from actual Qt widgets using
`tools/capture_example_screenshots.py`. The isolated, user-approved example
devices and masked account labels are not real account data. Desktop contrast
checks meet AA on used token/material surfaces; the offscreen backend does not
verify native Windows wallpaper blur. The website's prior desktop, 768px and
375px checks passed navigation, keyboard focus, image originals and overflow;
the retained delivery gate below records that scope.

## Design delivery gate

The following gate records the verified desktop/website direction and website
delivery checks. Runtime authentication and physical-model limits are recorded
separately above. A version/copy update must also pass the production HTML tests
and browser checks before deployment.

| Gate | Desktop: PASS evidence | Website: PASS evidence |
| --- | --- | --- |
| R-01 | blue identifies action/focus, selected targets and measured energy; semantic colors identify real states; no decorative gradient added. | blue identifies downloads, links and focus; neutral surfaces support product captures. |
| R-02 | UI string scan found no newly authored em dashes; remaining matches are existing module documentation/comments. | newly authored page copy has no em dashes; the existing document title is unchanged. |
| R-03 | desktop minimum/roomy windows, five account sizes and both compact dialogs have no horizontal overflow; assertions in the geometry/render logs pass. | desktop, tablet and phone layouts were rendered; no horizontal document overflow. |
| R-04 | existing device, transport, navigation and command icons describe their controls; no decorative emoji added. | Windows/Linux and task glyphs identify their actual platforms or features. |
| R-05 | login form/list, device grid/inspector, sync controls, pixel workspace and sectioned settings vary according to the actual task. | hero, feature summary, product captures, comparison table and downloads serve different tasks. |
| R-06 | existing Windows UI typography is retained for legibility; meter sizes distinguish present power and consumption from supporting labels. | existing system typography is retained; heading/body/caption sizes separate hierarchy. |
| R-07 | neutral workspaces support lighting control; the Draw grid represents actual editable pixels, with no decorative background pattern. | solid backgrounds contain no decorative patterns. |
| R-08 | no decorative CTA arrows; dropdown chevrons belong to actual selectors. | download glyphs describe file actions; no extra decorative CTA arrows were introduced. |
| R-09 | Default/selection/readback labels describe real device state; no novelty badges or duplicate eyebrow badge added. | redundant eyebrow badges removed; example captions describe the captures. |
| R-10 | translucency is restricted to native window foundation/navigation; forms, cards and controls use solid surfaces. | solid website surfaces; no decorative glass panels. |
| R-11 | existing 8/10/14px corner hierarchy retained; only actual power/selection controls use circular shapes. | established corner sizes remain consistent across buttons and screenshots. |
| R-12 | repeated card shadows removed; hierarchy comes from placement, spacing, surface and typography. | repeated shadows removed; spacing, headings and borders establish hierarchy. |
| R-13 | repeated power glows and status-light glow removed; blue borders carry keyboard focus. | no decorative glows; blue outlines identify keyboard focus. |
| R-14 | device cards repeat a real quick-control task, with capability-specific contents; login, readings, drawing and settings use different structures for their tasks. | captures represent real app views; the family comparison uses a native table. |
| R-15 | Sign in, Refresh devices, Load usage, Start/Stop, Send to Panel and mode commands name the action. | Download, Read account setup and Release notes name their destinations. |
| R-16 | new copy describes concrete actions/state without AI marketing terms. | copy describes controls, account setup, measured quantities and connection limits. |
| R-17 | watts/volts/amps/kWh come from device schemas/vendor responses; only explicitly identified verification fixtures supply sample numbers. | displayed watts and kWh are clearly labelled example data from the approved fixtures. |
| R-18 | no testimonials, fictional customers or avatars were added. | no testimonials, fictional users or invented social proof. |
| R-19 | brief navigation/expand/switch/notification transitions explain changes; status lights have no endless pulse; MOTION 2 is documented. | brief hover/focus and section-navigation feedback; no looping decoration. |
| R-20 | capability controls, real lighting state, LED mapping, pixel editing and energy usage express this product's purpose. | actual account, device and plug screenshots identify LumiSync's current functionality. |
| R-21 | existing dark identity is appropriate to display-adjacent lighting use; no light-theme toggle is advertised or left unfinished. | existing charcoal identity retained; no incomplete theme control advertised. |
| R-22 | no illustration was added; verification uses the app's actual rendered widgets. | screenshots are actual rendered widgets; no illustrative substitute for product UI. |
| R-23 | existing brand/navigation retained. The user's request to distinguish hardware authorizes four new semantic SVG glyphs (plug, wall switch, strip, matrix); source assets are packaged, and existing bulb art supplies the generic fallback. Samples are identified as fixtures. | existing brand assets and user-authorized example captures are used. |
| R-24 | all five navigation destinations open their real stack pages; keyboard/click assertions pass. | navigation anchors, account guide and release links reach their real destinations. |
| R-25 | normal text and composited material text meet 4.5:1; primary/danger and 96 colored mapping-label cases pass computed checks. | 78 rendered text elements pass computed contrast; minimum 7.32:1. |
| R-26 | the interaction table records real callbacks and resulting state; unavailable actions are disabled with task-specific explanations. | screenshot links open originals; binary URLs return their actual public release files. |
| R-27 | empty accounts/devices/targets, busy sign-in/discovery, verification, failed history and update errors all have useful next actions. | unsupported models/connections and missing measurements have concrete setup/availability guidance. |
| R-28 | no FAQ was introduced; help describes the actual selected vendor or device task. | no FAQ or generic filler; setup links support the described account task. |
| R-29 | existing charcoal neutrals and one blue accent retained; success/warning/error and actual light colors are semantic/data colors. | existing charcoal neutrals and blue accent retained; screenshot colors are product data. |
| R-30 | existing LumiSync components/navigation retained; no popular-product layout or branding cloned. | LumiSync branding and actual product screenshots; no borrowed product identity. |
| R-31 | major decisions and their one-line purpose are written in the direction above. | layout, type, color, icon and motion purposes are in the direction above. |
| R-32 | keyboard navigation, Enter submission, switches, canvas painting, mapping swaps, chart selection, focus pixels and dialog close checks pass. Inventory cards now support Enter/Space with a visible focus border in all three material variants. | keyboard Tab focus and Enter navigation exercised; screenshot links have accessible names. |
| R-33 | production Python/QSS changes were made directly with source patches; preview scripts render/test existing widgets without rewriting production source. | production source edited directly; capture tooling renders the real desktop widgets. |
| R-34 | solid/Mica/Acrylic selectors render every destination without horizontal overflow; primary/input keyboard focus is verified in all three. | responsive website behavior verified; no alternate website theme is declared. |
| R-35 | the real app was run/rendered, the 429-test suite and recorded interactions pass, and both frozen variants receive executable smoke checks. Inventory renders cover the user's 1120px window and stable heading/actions; real worker stress covers 1,200 rapid interactions, final readback and safe shutdown. The actual fault-output/masking subprocess passes. | production build/tests, browser interactions, public deployment and downloads verified. |
| R-36 | no unsupported security, compliance, device-certification or performance claims are made; live-device limits are stated below. | cloud manual-control/local-sync distinction and firmware/meter availability limits stated. |
| R-37 | existing LumiSync identity and the desktop lighting-control design read were declared before generation; rationale records the expanded scope and motion dial. | existing identity and desktop lighting-control purpose declared before redesign. |
| R-38 | production fields stay empty or use descriptive examples; chart gaps stay missing; every rendered sample is identified as a fixture. | every product capture says Example data; no fabricated public metrics. |
| C-1 | rationale gives a task-related reason for layout, typography, spacing, surfaces, focus and motion. | written rationale ties hierarchy, captures, type and controls to product understanding. |
| C-2 | recorded interactions reach real state changes and callbacks; no inert new controls. | new links reach the public guide, release, original images and binary downloads. |
| C-3 | each section serves connection, control, readings, sync, drawing or configuration; no template metrics/feed sections. | every section supports understanding, setup, compatibility or downloading LumiSync. |
| C-4 | compact/roomy layouts, empty/populated/error/busy states and keyboard operations pass; advanced content keeps scrolling available. | desktop/tablet/phone and keyboard behavior checked; examples remain legible full-size. |
| C-5 | verification distinguishes fixtures, user-confirmed login and unverified physical behavior. | sample data and model/firmware limits are explicit; tests are distinguished from physical validation. |
| UI-01 | palette derives from the existing token file and written identity. | palette follows the existing charcoal/blue identity. |
| UI-02 | blue marks action/focus/selection/data instead of decorating every surface. | accent identifies action, links and focus. |
| UI-03 | no decorative emoji in new headings, bullets or buttons. | no decorative emoji in headings, buttons or lists. |
| UI-04 | form/list, inspector, sync workspace and canvas compositions follow RHYTHM 2. | task-specific layouts follow RHYTHM 2. |
| UI-05 | no bento, fake terminal, pricing template or meaningless stripe added; navigation/error edges identify actual state. | no bento, fake terminal, pricing template or meaningless decorative stripe. |
| UI-06 | no duplicate pill badge above a page heading. | no repeated badge above headings. |
| UI-07 | real navigation destinations and control callbacks recorded above. | navigation, setup/release links, image originals and download targets verified. |
| UI-08 | MOTION 2 matches brief functional transitions, with no endless status animation. | brief functional transitions match MOTION 2. |
| UI-09 | two material foundation/navigation layers at most, no repeated shadows/glows, established radius hierarchy. | solid surfaces and established corner hierarchy; no repeated glow/shadow stack. |
| UI-10 | status lights reflect connection/activity; the Auto Director swatch reflects actual output or visibly waiting state. | product captures show labelled fixture device states; no fake live website indicators. |
| UI-11 | each screen centers its user's decision rather than a generic stat/chart/table shell. | sections focus on product capability, setup or obtaining the app. |
| UI-12 | production readings/counts/history use actual data; rendered samples are isolated fixtures. | approved example readings have explicit captions; no public adoption metrics invented. |
| UI-13 | unfilled inputs use descriptive placeholders; missing readings say Not reported rather than invented values. | website requests no credentials; captures have empty password inputs and masked example accounts. |
| UI-14 | empty/busy/error feedback names the task and a useful next action. | connection and availability limits point to the actual setup guide. |
| UI-15 | tested breakpoints/material variants and keyboard operations pass; minimum-height/advanced content uses intentional scrolling. | responsive layouts, keyboard focus and measured text contrast pass. |

