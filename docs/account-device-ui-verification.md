# LumiSync UI verification, 8 October 2026

Status: **PASS** for the implemented Windows UI scope. This report covers
Accounts, Devices and its inspector/meters, Monitor Sync, Music Sync, Draw,
Settings, Add Device, Panel tools, navigation and notifications. It records
interaction and layout verification; it is not a claim that every vendor or
physical device has been tested.

The user selected antislop-ui **during implementation**, then expanded the work
to the other app screens. The direction preserves LumiSync's existing dark
identity. See [the design rationale](account-device-ui-design.md).

## Findings fixed

| Finding | Result |
| --- | --- |
| Technical login fields competed with email/password | Visible vendor choices, conditional alternatives, editable country dropdown, collapsed package setup, separate verification step |
| Small account windows required scrolling for normal login | Compact inline fields; Sign in / Connected accounts sections on narrow windows; primary action pinned beside Close |
| Account country started with an unrelated default | Local Windows home-region suggestion, locale fallback, remembered provider country and manual override; no IP lookup |
| Find Devices omitted account refreshes | Refreshes every linked account and saved device status alongside local scans; one failure does not discard successful results |
| Narrow device inspectors squeezed or clipped their controls | Inspector uses the main pane on narrow windows; short metered views can place readings beside usage |
| Card columns left a large unused strip and pushed devices below the viewport | Columns fill the viewport and balance rows; six devices fit three columns at both screenshot sizes |
| The inventory disappeared despite enough space beside controls | Pane layout reserves two readable columns beside controls; both panes remain visible at 1120px, 1143px and 1297px app widths |
| Opening a device moved the description and wrapped page actions | Heading and toolbar span both panes; the inspector starts level with the cards; geometry checks preserve heading/action positions through open, change and close |
| The 1120px window became a single tall column beside a device | A 400px inspector and readable 260px card minimum retain two columns, including scrollbar allowance; all six cards fit at 1120×792 |
| Simple controls sat inside a tall empty frame | Inspector hugs content height and expands for details; a fixture switch uses 167px of the 596px content viewport |
| Plugs, switches, strips and panels looked alike | Metadata-based hardware glyphs and explicit type labels in cards and inspectors; generic fallback for unknown models |
| Meter readings waited 30 seconds and targeted refreshes could be discarded | Immediate visible read and five-second polling; coalesced identity-based queue, safe GUI-thread result delivery and cleanup |
| Unavailable device status could remain labelled Refreshing | Reported offline status now takes precedence over the stale flag |
| Detailed information competed with everyday controls | Unsupported controls hidden; device details and daily readings expandable; actual watts and monthly kWh lead their sections |
| Device refresh could shift group selections to another row | Selections and the open inspector follow stable device identities through reordering/removal |
| Music start controls fell below settings | Pinned Start/Stop; reactions and output beside each other in roomy windows; reaction dropdown in short windows |
| Draw tools overlapped in short windows | Tools reflow above the canvas; Send/Stop remain outside workspace scrolling; animation tools expand on demand |
| Settings switch labels were cut off | Wrapping labels/form rows and a section dropdown on narrow windows |
| Panel tool inputs mixed unrelated commands | Clock, Display, Countdown and Scoreboard tabs place settings beside their command |
| Some text/focus lost contrast under material selectors | Stronger muted text and material tint, solid control surfaces, explicit primary/input focus rules |
| Repeated card shadows/glows/pulses competed for attention | Matte cards, steady status lights and state text; only short functional transitions remain |
| Canvas/mapping required a mouse for editing | Keyboard pixel painting/erasing and keyboard LED-zone swaps; focus indicators retained |
| Notifications lacked an explicit dismiss control | Accessible dismiss button; external notification messages use plain text |

## Executed checks

- `python -m unittest discover -s tests`: **424 tests pass**, including 15 inventory
  layout/polling regressions, 14 broader
  app interaction tests, 18 account/meter UI tests and 8 combined-discovery/UI
  tests. Vendor protocol, storage, capability and worker regressions remain in
  the same suite. Nine additional stability/diagnostic regressions cover native
  cleanup, command bursts, old replies, timer ownership, closing and fault output.
  Test log: `build/vendor-audit/device-stability-tests.log`.
  The file-write rollback fixture intentionally logs a mocked replacement
  failure; the suite finishes with `OK`.
- `ruff check lumisync tests`, `compileall -q lumisync`, source
  `--check-integrations` and `--check-gui`: **PASS**.
- The actual Qt main window was rendered at **1120×760** and **800×520**, with
  empty data and isolated device fixtures. All five destinations and all five
  Settings sections have zero horizontal scroll. Monitor/Music Start, mode
  brightness, compact Music palette/normalization, and Draw's basic controls
  fit within their visible panes. Log: `build/vendor-audit/ui-app-layout.log`.
- The actual main window with six fixture devices was also rendered at
  **1120×792**, **1143×792**, **1297×792**, **1600×900** and **800×520**. At the first four
  sizes the inventory needs no vertical or horizontal scroll for basic controls;
  rows are balanced in three columns. The inspector retains two inventory
  columns at 1120/1143/1297px and three at 1600px. At the minimum
  size the inspector occupies the main pane and the inventory can scroll.
  The inspector aligns with the first card row; headings and page actions do
  not move on opening/closing. Hidden group UI contributes no spacer. Card rows
  use modest spare height, capped at 196px. Short switch inspectors use 167px
  instead of filling their 596px/704px viewport. Log:
  `build/vendor-audit/ui-inventory-layout.log`; screenshots:
  `build/vendor-audit/ui-inventory-*.png`.
- Visible metering requests immediately, keeps its timer through repeated
  metadata updates, and stops on close, hide or navigation away. The timer is
  configured to five seconds; fixture timing tests shorten the interval. A real
  Qt event loop and status QThreads deliver successive watts to the displayed
  inspector, with I/O replaced by an adapter fixture. Recorded assertions check
  background query execution, GUI-thread delivery, identity-based queuing,
  reorder/removal, retained values and the offline label. Windows thread tests
  use the production event loop; `QTest.qWait` prevented reliable Python worker
  progress in this environment. No personal account requests were sent.
  Explicit queued delivery follows [Qt's thread-affinity guidance](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html#thread-affinity).
- Solid, Mica and Acrylic CSS variants were rendered across all destinations.
  Actual rendered primary-button/input border pixels verified visible keyboard
  focus in each variant. The offscreen backend does not reproduce native DWM
  wallpaper blur; composited contrast was checked separately.
- Account forms for all three vendors were rendered at **860×800**, **860×620**,
  **560×700**, **480×600** and **480×440**. Normal login fields need no scrolling
  at the first four sizes. At the minimum 440px height the form can scroll;
  submission and Close remain pinned. Connected-account actions fit at every
  narrow size. Log: `build/vendor-audit/ui-geometry.log`.
- Metered inspector fixtures were checked at **1040×600**, **760×600**,
  **760×520** and **1280×900**. Power, Refresh and Load usage remain reachable.
  Daily charts/tables and technical details can scroll when expanded.
- Add Device's three transport forms were rendered at **480×520**; its action
  footer stays visible. All four Panel tools tabs were rendered at **520×440**
  and **380×440**, with tab labels and command buttons contained.
- Text contrast was computed against actual token colors, primary alpha fills,
  and material fills composited over a white desktop. Normal/muted text meets
  4.5:1 on its used surfaces. Primary-button text is at least **6.29:1**; danger
  text is at least **4.96:1**; the daily energy series has **5.36:1** contrast.
  Colored mapping-label fixtures also pass AA after compositing.
  Log: `build/vendor-audit/ui-contrast.log`.
- Both Windows packaging variants are built in `dist/device-stability/` and each
  passes `--help`, `--check-integrations` and `--check-gui` with exit code 0.
  The GUI check constructs, renders and closes the actual application using
  isolated settings. Log: `build/vendor-audit/device-stability-build-smoke.log`.

All visual data is isolated fixture data. The harness loads installed Segoe UI
font files for the offscreen plugin; production font selection is unchanged.
Personal account sessions, saved settings and physical outputs were not used
for this UI verification.

The current standalone executable is
`dist/device-stability/LumiSync-Windows-x64-onefile.exe`.
Size: 96,485,506 bytes. SHA-256:
`4BA99FC9E988C5C552A5DA3F247F90A6AA9D0A9127F12BAC09416E6213F04BFF`.
Older executable folders and running app processes were preserved.

## Reproduced crash and stability checks

Windows Application events record the user's 16:07:34 onefile crash as
`0xc0000374` in `ntdll.dll`. The existing app log stops after successive Govee
LAN commands, with no Python exception. A subprocess using actual Qt widgets,
command tasks and status QThreads reproduced the same heap exception while
releasing the status worker in `_clear_status_refs`.

Worker ownership now continues until `QThread.wait(0)` confirms native cleanup
has joined; `finished`/`isRunning()` alone did not establish that. The GUI retries
cleanup without blocking. The same lifetime rule covers discovery, Bluetooth,
sync, drawing, update checking and shutdown. See
[Qt's finished-signal and synchronization documentation](https://doc.qt.io/qt-6/qthread.html#finished).

Pending commands keep the latest value for each control. Each device has one
owned confirmation timer sequence, restarted after a new write, with identity
resolved when it fires. Old status reads cannot undo a newer command; readback
leaves sliders alone while dragging or awaiting debounce. Typed timer slots
avoid retaining the controller through a timer closure.

The isolated stress subprocess completes **1,200 interactions, 853 writes and
74 status reads**, with at most two pending values (power and brightness), all
updates delivered on the GUI thread, and confirmed final **power off / 73%**.
Counts of coalesced writes vary with scheduling. Evidence:
`build/vendor-audit/device-controls-baseline.log` and
`build/vendor-audit/device-controls-stress-final.log`;
regression source: `tests/helpers/device_controls_stress.py`.

Native fault stacks persist in `~/.lumisync/logs/lumisync_fault_DATE_PID.log`.
Qt warnings and uncaught background exceptions also reach the existing logs;
Qt message email/token fields are masked and background diagnostics omit
exception values/locals. The subprocess test writes and reads an actual fault
trace and checks masking. **Settings → About → Open logs folder** dispatches
to the local log directory in a recorded UI callback test.

## Recorded interaction coverage

Qt mouse/key events and real widget callbacks were used. Hardware commands,
vendor network calls, credential writes, autostart writes, file pickers and
external URL opening were replaced at their I/O boundaries. This verifies
dispatch, state and feedback without claiming live vendor acceptance.

| Screen/control | Executed action and observed result |
| --- | --- |
| Main navigation | Down-arrow changes the four main pages; Settings click opens its real stack page |
| Provider buttons/method selector | Govee, Tuya and LSC clicks show the correct password/API/QR fields |
| Account inputs/country | Typed fixture credentials; provider-specific remembered country and manual changes stay separate; successful metadata uses ISO country and a masked label |
| Password Show/Hide | Changes echo mode; switching provider resets masking |
| Sign in / Connected accounts selectors | Switch panels, preserve entered values and keep primary actions visible |
| Sign-in submission/Enter | Empty submission focuses validation; a valid fixture account reaches success/import without persisting its password |
| Verification code/Back/resend | Empty code retains the session, Back discards pending credentials, resend reuses the pending client |
| App setup/Browse/Remember | Expands/collapses setup; picker selection populates the package field; Remember changes state |
| QR request/approval | Produces a QR pixmap; approval first shows pending, then saves the same fixture client's session |
| Account list/refresh/disconnect | Selected fixture account refreshes/imports; disconnect removes its session and displays the empty state |
| Account Close/busy state | Dialog closes normally; busy state prevents duplicate submission and early closure |
| Find Devices | Toolbar dispatches discovery; separate worker tests refresh all accounts, handle partial failure, queued status and local/cloud arrival races |
| Add Device/Accounts toolbar actions | Real modal dialogs open and close without issuing requests |
| Device card power/brightness/details | Correct device index dispatched; slider commits after debounce; card opens its inspector |
| Inspector power/brightness/white/color | Correct device index and chosen value/color reach controller callbacks |
| Inspector details/default/connection/panel tools | Details expand; default selection dispatches; connection dropdown chooses Account Wi-Fi; a matrix panel's tools dialog opens and closes |
| Inspector zones/reset/remove/close | Zone dialog sends chosen count; reset clears override; removal requires the actual confirmation branch; Close restores the device grid |
| Device group controls | Create Group enters safe selection mode; card picks a member; Save Group names/saves those indices; Cancel exits |
| Device refresh reordering | Open device and selected group members stay attached to identity; removal clears stale selection |
| Electrical Refresh/month/Load usage | Refresh signal fires; month selection and load render fixture history; cache and failed request retain live readings |
| Daily chart/table | Arrow keys inspect exact values; zero differs from missing; daily toggle reveals the exact read-only rows |
| Monitor/Music Start and Stop | Selected local fixture devices reach start callbacks; running state changes the same button to Stop and dispatches stop |
| Music reaction library | Every reaction button persists its key/description; compact dropdown and buttons stay synchronized |
| Palette/brightness/normalization | Keyboard selection persists palette/brightness; checkbox changes normalization state |
| Sync targets/group/zones | Targets toggle participation and disable impossible starts; group name dispatches to save; zones dispatch to the selected target strip |
| LED mapping toggle | Opens/closes mapping without hiding the pinned sync action; I/O ownership boundaries are mocked |
| Mapping preview/reverse/reset/depth/test | Keyboard swaps two zones; Reverse changes order; Reset confirms and restores mapping; depth updates; Test/Stop changes state and feedback |
| Draw brush/fill/clear/canvas | Chosen brush color paints/fills actual grid; clear honors confirmation; mouse and keyboard paint, Delete erases and arrows stay bounded |
| Draw target/size | Eligible panel is selected; changing size can cancel or confirm clearing drawing/frames |
| Draw Animation/frame controls | Toggle reveals controls; Add Frame stores copies; two frames enable playback; Clear Frames honors confirmation |
| Draw Send/Play/Stop | Canvas or saved-frame data reaches the send boundary; Stop dispatches; resizing preserves art and saved frames |
| Settings sections | Dropdown and section list remain synchronized through narrowing/widening; every section opens |
| Tray/startup/status/material | Switches change settings; keyboard toggles work; autostart reaches the mocked OS boundary; material selection persists |
| Monitor/music tuning/display | Sliders and gamma switch update settings; display choice reaches the selected screen setting |
| Settings groups/About | Delete confirms and dispatches; update check shows loading, error and available-release feedback; release/repository links dispatch real GitHub URLs |
| Settings log folder | Open logs folder dispatches the local logs directory through QDesktopServices; the OS boundary is mocked |
| Panel tools | All four tab clicks open corresponding controls; selected clock/rotation/countdown/score values reach adapter methods; busy/error/close feedback works |
| Manual Add Device | Each transport rejects empty required data visibly; valid fixture inputs accept; submit remains reachable |
| Notifications | Repeated brightness messages coalesce; plain text retained; explicit dismiss removes the notification |

`tests/test_responsive_app_ui.py`, `tests/test_account_device_ui.py`,
`tests/test_find_devices_refresh.py`, `tests/test_device_inventory_ui.py`, `tests/test_device_control_stability.py`
and `tests/test_device_ui_state.py`
contain the recorded callbacks and assertions. Additional vendor/device
fixture tests cover their underlying implementation.

The inventory follow-up also records real card clicks, Enter/Space opening,
manual refresh dispatch to the inspected device, updated displayed watts,
close/navigation stopping polling, and content-height changes when details
expand. Very short windows give the inspector a wider body, allowing electrical
readings and monthly usage to sit beside each other; power/Refresh/Load usage
stay visible. Expanded daily details keep intentional vertical scrolling.
New hardware glyphs use a label as well as their silhouette, never color alone.

## Antislop delivery gate

Every line below is a PASS for this UI scope, with its evidence stated.

### Hard gate

- **R-02 PASS:** UI string scan found no newly authored em dashes; remaining matches are existing module documentation/comments.
- **R-03 PASS:** desktop minimum/roomy windows, five account sizes and both compact dialogs have no horizontal overflow; assertions in the geometry/render logs pass.
- **R-17 PASS:** watts/volts/amps/kWh come from device schemas/vendor responses; only explicitly identified verification fixtures supply sample numbers.
- **R-18 PASS:** no testimonials, fictional customers or avatars were added.
- **R-23 PASS:** existing brand/navigation retained. The user's request to distinguish hardware authorizes four new semantic SVG glyphs (plug, wall switch, strip, matrix); source assets are packaged, and existing bulb art supplies the generic fallback. Samples are identified as fixtures.
- **R-24 PASS:** all five navigation destinations open their real stack pages; keyboard/click assertions pass.
- **R-25 PASS:** normal text and composited material text meet 4.5:1; primary/danger and 96 colored mapping-label cases pass computed checks.
- **R-26 PASS:** the interaction table records real callbacks and resulting state; unavailable actions are disabled with task-specific explanations.
- **R-27 PASS:** empty accounts/devices/targets, busy sign-in/discovery, verification, failed history and update errors all have useful next actions.
- **R-28 PASS:** no FAQ was introduced; help describes the actual selected vendor or device task.
- **R-32 PASS:** keyboard navigation, Enter submission, switches, canvas painting, mapping swaps, chart selection, focus pixels and dialog close checks pass. Inventory cards now support Enter/Space with a visible focus border in all three material variants.
- **R-33 PASS:** production Python/QSS changes were made directly with source patches; preview scripts render/test existing widgets without rewriting production source.
- **R-34 PASS:** solid/Mica/Acrylic selectors render every destination without horizontal overflow; primary/input keyboard focus is verified in all three.
- **R-35 PASS:** the real app was run/rendered, the 424-test suite and recorded interactions pass, and both frozen variants receive executable smoke checks. Inventory renders cover the user's 1120px window and stable heading/actions; real worker stress covers 1,200 rapid interactions, final readback and safe shutdown. The actual fault-output/masking subprocess passes.
- **R-36 PASS:** no unsupported security, compliance, device-certification or performance claims are made; live-device limits are stated below.
- **R-37 PASS:** existing LumiSync identity and the desktop lighting-control design read were declared before generation; rationale records the expanded scope and motion dial.
- **R-38 PASS:** production fields stay empty or use descriptive examples; chart gaps stay missing; every rendered sample is identified as a fixture.

### Purpose gate

- **R-01 PASS:** blue identifies action/focus, selected targets and measured energy; semantic colors identify real states; no decorative gradient added.
- **R-04 PASS:** existing device, transport, navigation and command icons describe their controls; no decorative emoji added.
- **R-06 PASS:** existing Windows UI typography is retained for legibility; meter sizes distinguish present power and consumption from supporting labels.
- **R-07 PASS:** neutral workspaces support lighting control; the Draw grid represents actual editable pixels, with no decorative background pattern.
- **R-08 PASS:** no decorative CTA arrows; dropdown chevrons belong to actual selectors.
- **R-09 PASS:** Default/selection/readback labels describe real device state; no novelty badges or duplicate eyebrow badge added.
- **R-10 PASS:** translucency is restricted to native window foundation/navigation; forms, cards and controls use solid surfaces.
- **R-12 PASS:** repeated card shadows removed; hierarchy comes from placement, spacing, surface and typography.
- **R-13 PASS:** repeated power glows and status-light glow removed; blue borders carry keyboard focus.
- **R-14 PASS:** device cards repeat a real quick-control task, with capability-specific contents; login, readings, drawing and settings use different structures for their tasks.
- **R-19 PASS:** brief navigation/expand/switch/notification transitions explain changes; status lights have no endless pulse; MOTION 2 is documented.
- **R-22 PASS:** no illustration was added; verification uses the app's actual rendered widgets.

### Liveliness

- **Dials PASS:** ENERGY 1 / RHYTHM 2 / MOTION 2 explicitly recorded in the design direction.
- **Dial consistency PASS:** quiet neutral surfaces, varied task layouts and brief functional transitions match those dials.
- **Focal point PASS:** sign-in submission, device power/readings, sync Start/Stop, editable canvas and the selected Settings section lead their screens.
- **Structural whitespace PASS:** labels stay near fields; groups separate authentication, management, controls, readings and history; compact layouts reduce structural margins.
- **Deliberate accent PASS:** one blue accent marks action, focus, selection or the measured series; semantic and artwork colors carry data.
- **Identity motif PASS:** charcoal workspace, blue action/focus border and nearby labels repeat across accounts, devices and mode controls.
- **Design read PASS:** desktop lighting control for people managing their own lights/plugs in LumiSync's existing dark visual language was declared before generation.

### Craftsmanship and quality locks

- **C-1 PASS:** rationale gives a task-related reason for layout, typography, spacing, surfaces, focus and motion.
- **C-2 PASS:** recorded interactions reach real state changes and callbacks; no inert new controls.
- **C-3 PASS:** each section serves connection, control, readings, sync, drawing or configuration; no template metrics/feed sections.
- **C-4 PASS:** compact/roomy layouts, empty/populated/error/busy states and keyboard operations pass; advanced content keeps scrolling available.
- **C-5 PASS:** verification distinguishes fixtures, user-confirmed login and unverified physical behavior.
- **R-05 PASS:** login form/list, device grid/inspector, sync controls, pixel workspace and sectioned settings vary according to the actual task.
- **R-11 PASS:** existing 8/10/14px corner hierarchy retained; only actual power/selection controls use circular shapes.
- **R-15 PASS:** Sign in, Refresh devices, Load usage, Start/Stop, Send to Panel and mode commands name the action.
- **R-16 PASS:** new copy describes concrete actions/state without AI marketing terms.
- **R-20 PASS:** capability controls, real lighting state, LED mapping, pixel editing and energy usage express this product's purpose.
- **R-21 PASS:** existing dark identity is appropriate to display-adjacent lighting use; no light-theme toggle is advertised or left unfinished.
- **R-29 PASS:** existing charcoal neutrals and one blue accent retained; success/warning/error and actual light colors are semantic/data colors.
- **R-30 PASS:** existing LumiSync components/navigation retained; no popular-product layout or branding cloned.
- **R-31 PASS:** major decisions and their one-line purpose are written in the linked design rationale.

### UI skill checklist

- **UI-01 PASS:** palette derives from the existing token file and written identity.
- **UI-02 PASS:** blue marks action/focus/selection/data instead of decorating every surface.
- **UI-03 PASS:** no decorative emoji in new headings, bullets or buttons.
- **UI-04 PASS:** form/list, inspector, sync workspace and canvas compositions follow RHYTHM 2.
- **UI-05 PASS:** no bento, fake terminal, pricing template or meaningless stripe added; navigation/error edges identify actual state.
- **UI-06 PASS:** no duplicate pill badge above a page heading.
- **UI-07 PASS:** real navigation destinations and control callbacks recorded above.
- **UI-08 PASS:** MOTION 2 matches brief functional transitions, with no endless status animation.
- **UI-09 PASS:** two material foundation/navigation layers at most, no repeated shadows/glows, established radius hierarchy.
- **UI-10 PASS:** status lights reflect connection/activity; the Auto Director swatch reflects actual output or visibly waiting state.
- **UI-11 PASS:** each screen centers its user's decision rather than a generic stat/chart/table shell.
- **UI-12 PASS:** production readings/counts/history use actual data; rendered samples are isolated fixtures.
- **UI-13 PASS:** unfilled inputs use descriptive placeholders; missing readings say Not reported rather than invented values.
- **UI-14 PASS:** empty/busy/error feedback names the task and a useful next action.
- **UI-15 PASS:** tested breakpoints/material variants and keyboard operations pass; minimum-height/advanced content uses intentional scrolling.

## 0.8.0 About and documentation follow-up

- **PASS:** About identifies version 0.8.0, supported device families, personal
  accounts and supported metering, with Minlor/MIT credit. Two action rows keep
  support controls together without stretching a sparse page.
- **R-03 / R-24 / R-26 / R-32 / R-35 PASS:** the Settings interaction test opens
  repository, account guide, update release and the logs folder through the
  actual callbacks. About fits at 712×488 and 1020×760 without either scrollbar,
  including the visible update-release action. Existing keyboard focus styles
  remain in use. Native folder URLs use a temporary platform-native test path.
- **R-17 / R-18 / R-23 / R-38 PASS:** new README captures render actual widgets
  with explicitly named examples. The isolated capture utility uses disposable
  settings and patched hardware/account reads. Example readings match the
  previously reviewed 215.1 W / 233.8 V / 1.023 A and 25.500 kWh demonstration.
- **PASS:** 424 source tests pass on Windows with Qt 6.11.1 and, separately,
  a clean install of the latest dependencies with Qt 6.12.0. UI-state tests now
  close their owned widgets/controllers and process deferred deletion, avoiding
  interpreter-exit native crashes in the newer Qt bindings. Ruff passes.
- **Delivery gate PASS:** the previously recorded core/UI checklist continues
  to apply to this About change: the same palette, type, surfaces and motion;
  concrete copy and actions; no new decorative technique or unsupported claim.

## Validation limits

Live LSC plug electrical readings and energy history still need hardware
validation. The user has confirmed Govee, LSC and Tuya personal-account login;
this UI pass does not repeat those logins or issue physical commands. Native
DWM blur and every monitor/DPI/hardware combination cannot be established by
offscreen rendering. Qt keeps DPI-aware logical sizing and the solid fallback.

At extreme dialog heights or with expanded advanced settings, daily tables,
many devices/groups or animation tools, scrolling remains intentional. Basic
controls are kept reachable at the sizes listed above; the report does not
claim every possible amount of content fits without scrolling.
