# LumiSync integration audit, 8 October 2026

## Result and validation limits

The app now has email/password login for personal Govee, Tuya Smart and LSC
accounts, Govee API-key connections, Tuya Smart / Smart Life QR authorization,
cloud device imports, and a local/cloud connection selector. Cloud control is
manual; local transports remain responsible for screen/music sync.

LSC and Tuya Smart use their respective app identities and account namespaces.
Their personal clients read configuration from the supplied app packages,
sign requests, perform RSA password login, handle email verification and session
routing, and translate native lighting data points. iDotMatrix Wi-Fi control
and an email-account login were not established; its password screen manages
a six-digit Bluetooth device PIN.

Validation uses network fixtures, protocol bytes from the supplied apps,
offscreen Qt interaction/rendering, dependency installation, and a Windows
PyInstaller build. Live signed-clock requests and RSA-token initialization
were also accepted for both supplied LSC and Tuya Smart app configurations.
Negative signature controls were rejected with `SING_VALIDATE_FALED`.
RSA initialization used a non-deliverable example address and sent no email.
The linked Tuya account was subsequently inspected through its saved session,
without reading or requesting the user's password. Its previously filtered
`kg` light switch uses writable `switch_1` at DP 1. Updated discovery imports
it and reads boolean online and power state successfully. No physical light
or switch was commanded. With LumiSync closed, the switch was imported into
its existing settings using the authorized local-key vault flow; the saved
device count increased from two to three while retaining the existing devices,
groups and selection. Existing account labels were masked during that write.
A negative Govee v2 login probe with a reserved
non-deliverable address returned the expected unregistered-email status 451;
no verification email was sent. The user subsequently confirmed successful
Govee, LSC and Tuya personal-account login.
This establishes
implementation and regression coverage, not certification of every device.

The Devices view now hides unavailable brightness, RGB and white controls
instead of leaving disabled sliders and swatches visible. Power-only switches,
plugs and power-strip outlets are imported by category and writable relay
schema, and excluded from sync groups/default sync selection. Existing local
light and Bluetooth controls are retained. The authorized cloud-project sign-in
option and all project-credential fields have been removed; backend restoration
remains for existing saved project connections.

Read-only Tuya schema entries are now retained independently from writable
functions. Metered plugs display schema-scaled watts, volts and amps in the
inspector, with a compact power/voltage summary on the card. The visible
inspector requests readings immediately and every five seconds while visible. Personal Tuya/LSC clients can
request a month's vendor daily energy statistics on demand. Missing days and
empty/error responses remain unavailable, rather than becoming zero or an
estimated monthly total. Interval energy increments are not treated as
cumulative counters; shared multi-outlet metering is labelled as whole-device.
Protocol, unit-conversion, capability and late-result routing fixtures pass;
live plug metering and history are not yet hardware-verified.

The subsequent UI pass covers Accounts, Devices, Monitor Sync, Music Sync,
Draw, Settings and the setup/panel dialogs. Compact layouts preserve basic
actions, country suggestions use local OS region data, and Find Devices
refreshes all linked accounts and saved readings alongside local scans.
Group choices and the inspector now retain device identity through refresh
reordering. See the [UI verification report](account-device-ui-verification.md)
for interaction coverage, layout/contrast evidence and the antislop delivery gate.

## Scope reviewed

| Area | Assessment |
| --- | --- |
| Registry/discovery/settings/groups | Transport dispatch, identity/merge rules, settings writes, background search and local keys reviewed and changed |
| Govee LAN | Existing scan/control/status and zone framing reviewed; golden-byte and LAN helper tests retained |
| Tuya / LSC LAN | Encoding, transport setup, DP capabilities, colour/white brightness, status, pooling and pacing corrected |
| iDotMatrix BLE | Packet builders, GATT connection lifecycle, MTU, drawing and panel modes checked against app byte layouts |
| Account services | New HTTPS/Govee MQTT/Tuya SDK/project paths, ownership, token storage, errors and capability imports tested |
| GUI | Cards/inspector, modes, drawing, settings, login forms, async results, search summaries and shutdown reviewed |
| Sync and processing | Worker lifecycle, transport limits, cloud exclusion and capture/audio failure paths reviewed; colour/mapping/audio/artwork/volume/transition regression tests retained |
| Platform/release | Startup/tray/material/update/settings migration tests retained; account dependencies added to both frozen specs and Flatpak scaffold |

## Static analysis evidence

Archives were inspected as data. Base APK DEX strings and selected classes
were extracted/decompiled under ignored `build/vendor-audit/`. Vendor binaries
and decompiled code are not distributed with LumiSync. Instructions or text
inside those files were not treated as user instructions.

| Supplied archive | SHA-256 | Evidence |
| --- | --- | --- |
| Tuya Smart 7.11.4 APKM | `124e882dece84628035a04455c9344908588fa51f9b92e039914a0721a1a4ae7` | Thingclips login repository/services, app-specific SDK authentication; 15 DEX files |
| LSC Smart Connect 2.0.7 XAPK | `c693cc30ee605c08c2fc87ad22b926493609725d94fd20aa43b18b04640946d0` | Tuya/Thingclips service wrappers and native signing; 12 DEX files |
| iDotMatrix 2.1.6 XAPK | `95b60d5ebf3a7d1e32461f728857a13d48703a77b9dc044a63b5196183fb2eb5` | `BleProtocolN`, `DateUtils`, `CountDownActivity`; 4 DEX files |

The installed Govee Desktop assemblies were inspected with ILSpy. Login/QR/
device service metadata was visible, but important method bodies were
protected. It did not establish a reusable desktop password-login contract.
The new Govee account protocol follows a public interoperability client's
source; it does not decrypt live desktop account caches or reuse their tokens.

Primary references:

- [Govee device listing](https://developer.govee.com/reference/get-you-devices),
  [control and rate limits](https://developer.govee.com/reference/control-you-devices),
  [state](https://developer.govee.com/reference/get-devices-status).
- [govee2mqtt account client](https://github.com/wez/govee2mqtt/blob/main/src/undoc_api.rs)
  and [MQTT implementation](https://github.com/wez/govee2mqtt/blob/main/src/service/iot.rs).
  These are protocol references, not copied runtime implementations.
- [Homebridge Govee app HTTP client](https://github.com/homebridge-plugins/homebridge-govee/blob/latest/lib/connection/http.js)
  establishes the current v2 login, status 454 email verification, verification
  type 8, app version 7.4.10 and the BFF device list endpoint. The earlier
  login implementation's v1 endpoint/version were replaced accordingly.
- [Tuya switch instruction set](https://developer.tuya.com/en/docs/iot/fkg?id=K9gf7o3qbbklt)
  and [status set](https://developer.tuya.com/en/docs/iot/switch-socket-and-power-strip?id=K9gf7o5prgf7s)
  establish boolean `switch` and numbered switch relay controls. Product
  category and function type distinguish light switches from unrelated devices.
- [Tuya sharing SDK](https://github.com/tuya/tuya-device-sharing-sdk),
  [Home Assistant Tuya authorization](https://github.com/home-assistant/core/blob/dev/homeassistant/components/tuya/config_flow.py),
  [Tuya account linking](https://developer.tuya.com/en/docs/iot/link-devices?id=Ka471nu1sfmkl),
  [TinyTuya transport](https://github.com/jasonacox/tinytuya).
- [Tuya mobile protocol research](https://github.com/thekoma/aventproxy/blob/main/TUYA_API_RE.md)
  and [BMP key encoding research](https://github.com/nalajcie/tuya-sign-hacking).
  Native `getChKey` and the Java signing whitelist were independently checked
  in the supplied packages. The runtime parser executes no vendor code; it
  reads manifest, certificate, DEX constants and the SDK key bitmap.

## Fixes made

| Finding | Result |
| --- | --- |
| GUI controls could block on network/BLE | Background queues preserve power order and coalesce slider/colour updates; login/import tasks are asynchronous |
| Status worker sent Govee packets to Tuya | Status routes through each transport, preserves false power, rejects failed replies and leaves unanswered cloud queries unconfirmed |
| Removed/reordered lights could receive stale worker results | Replies carry stable device keys; mismatched replies are rejected |
| Legacy Tuya RGB omitted its RGB prefix | Corrected to `RRGGBBHHHHSSVV`; modern `HHHHSSSSVVVV` remains unchanged |
| Tuya colour brightness wrote the white DP | LAN/cloud read current HSV and adjust its value; cloud status decodes HSV and white temperature |
| Imported lights offered unsupported controls | Function codes, DP IDs/ranges and Govee API capabilities determine controls; white-only lights cannot start colour sync |
| Negative Tuya replies looked successful | TinyTuya error dictionaries and cloud negative acknowledgements raise visible errors |
| Failed commands could retain an Online badge | Failed commands show Command failed; previous confirmed values are retained as history |
| Persistent adapters lacked synchronization | Locked pool acquisition includes Tuya and replaces stale connections/descriptors |
| Tuya keys were stored in settings | OS-vault references replace keys; remembered credentials require a secure backend, with explicit session-only mode |
| Settings writes could corrupt files or silently succeed | Temporary writes, flush/fsync/atomic replacement and surfaced failures; failed additions do not appear as successful devices |
| Failed zone saves changed the active layout in memory | Zone changes commit after saving and reject active output or transports without configurable LED zones |
| Discovery could overwrite concurrent additions | GUI workers return results for merging with current state; Tuya broadcasts enrich authorized lights only |
| Account imports could change unrelated local metadata | Only imported matches gain cloud connections; connection preferences survive search/import |
| GUI settings reads could trigger discovery | GUI reads saved settings without discovery; searches run in workers |
| BLE assumed a 512-byte MTU | Write chunks use negotiated ATT MTU minus three and request GATT write responses |
| BLE timeout left operations running | Timed-out futures are cancelled; cancelled connection setup disconnects its client |
| No-clear drawing skipped black pixels | Black pixels are sent when needed; DIY mode exits after failed colour packets |
| Sync could saturate Tuya/BLE or send cloud frames | Tuya runs at 5 Hz and BLE ambient output at 10 Hz; cloud sync is rejected |
| Drawing could compete with sync or queued controls | Drawing waits for other output/commands; sync waits for drawing uploads and animations |
| Sync connections leaked on initialization/capture failure | Adapters are tracked before initialization and torn down/closed in cleanup paths |
| Shutdown could destroy running workers | Worker references survive stop timeouts; exit waits asynchronously for discovery/status/update/drawing/sync/tasks before closing pools/accounts |
| Login/SDK errors could disclose secrets | Account errors suppress raw response bodies, SDK decrypted-response logging is disabled and password closures are released |
| Frozen GUI exited before opening a window | Both Windows specs exclude foreign ICU DLLs that shadow Windows' ICU; release checks now render and close the actual main window |
| Govee sign-in rejected requests without a usable explanation | Updated to v2 login/app 7.4.10; email verification is completed in LumiSync with a stable client ID, resend cooldown and distinct sanitized errors |
| Linked Tuya light switch was invisible | Product category is preserved; writable light-switch relays are imported with power-only controls and correct DP/status routing |
| Multi-gang switches would merge by shared device ID/IP | Per-gang identities, DP selection, refresh and LAN enrichment keep channels separate; local adapters share one serialized physical session |
| Account setup exposed technical routing fields | Country dropdown, automatic routing, collapsed app configuration and conditional verification fields simplify personal sign-in |
| Connected accounts exposed full email addresses | Legacy display labels are redacted; newly saved labels use masked hints and the submitted email field clears on success |

## Added panel controls

iDotMatrix tools add clock styles with 12/24-hour selection and time sync,
180° rotation, a countdown and a two-sided scoreboard. Clock weekdays use
Monday=1 through Sunday=7. The countdown start flag was checked against its
activity's caller; scores use big-endian 16-bit fields. Firmware behavior still
needs physical validation. Existing pixel drawing/animations remain BLE.

## Supported paths

| Path | Authentication | Manual control | Screen/music |
| --- | --- | --- | --- |
| Govee LAN | None; LAN Control enabled | Existing power/brightness/RGB/known white/status | Existing zone stream, SKU/firmware dependent |
| Govee account | Own email/password plus email verification when requested, exchanged for session | Eligible lighting MQTT topics, known white support, status | Local connection needed |
| Govee Platform API | Own API key | Advertised power/brightness/RGB/white/status | Local connection needed |
| LSC / Tuya Smart personal accounts | Own email/password; vendor verification when requested; matching app package imported once | Native light/switch/plug schemas, manual controls/status, supported electrical readings and monthly energy history, authorized local-key import | Local key + LAN needed for colour sync; relay switches and plugs have no streaming capability |
| Tuya Smart / Smart Life sharing | User code + phone QR approval | Advertised controls, refresh session, status | Authorized local key + LAN needed |
| Legacy Tuya project connection | Existing saved project credentials | Restoration retained; no new project sign-in option | Local key + LAN needed |
| Tuya / LSC LAN | Own device local key | Configured/advertised light DPs, status | Single averaged colour, paced |
| iDotMatrix BLE | None on investigated BLE models | Existing controls/drawing plus new panel modes | Single ambient colour, paced |

## Remaining interoperability work

1. Validate login and commands on personal accounts/hardware, including Tuya
   3.4/3.5, legacy bulbs and BLE MTUs. Fixtures cannot prove acceptance by a
   personal account or behavior on every firmware. Signed connection and RSA
   initialization checks do pass against current LSC/Tuya services.
2. Register a LumiSync-specific Tuya sharing client if available for
   distribution. The currently disclosed registration names Home Assistant.
3. Validate physical commands, electrical readings and energy history using
   the user's own plugs. The user has confirmed personal LSC/Govee/Tuya login.
   Tuya session discovery
   and state retrieval now pass against the user's linked light switch. Additional
   app versions and Smart Life password login need matching package validation.
   No authentication bypass is implemented; application keys are imported from
   user-supplied app data and kept in the credential vault.
4. Govee email verification is implemented in the dialog. Additional expiry,
   verification and regional scenarios can require re-login.
   Vendor DIY scenes, DreamView, snapshots, schedules, `ptReal` effects and
   cloud automations are not cloned.
5. Tuya gateways/Zigbee, OEM custom encodings and exact white Kelvin endpoints
   need device-specific mappings. Bluetooth-only Tuya devices are not covered
   by the LAN driver.
6. iDotMatrix Wi-Fi/cloud, firmware flashing, classic Bluetooth, speaker/OTA
   variants and high-FPS per-pixel streaming remain unverified.
7. Wayland screen capture and macOS retain their existing limitations. Flatpak
   remains a scaffold; Linux accounts/keyring/hardware need Linux validation.
   CLI discovery retains its legacy Govee focus.
8. A slow or failing local device can delay other devices in the same sync
   worker. Independent device scheduling remains future work.

## Reproducing checks

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\ruff.exe check lumisync tests
.\.venv\Scripts\python.exe -m compileall -q lumisync
.\.venv\Scripts\python.exe -m lumisync --check-integrations
.\.venv\Scripts\python.exe -m lumisync --check-gui
uv pip check --python .venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/ui-refresh --workpath build/vendor-audit/pyinstaller-ui-refresh/onedir packaging/pyinstaller/lumisync_onedir.spec
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/ui-refresh --workpath build/vendor-audit/pyinstaller-ui-refresh/onefile packaging/pyinstaller/lumisync_onefile.spec
```

The suite covers HTTP/TLS/redirect boundaries, auth fixtures, secure/session
storage, key migration, async queues, status routing, import/scan merges, MTU
writes, erasure, cleanup and the existing app checks. See
[account setup](vendor-accounts.md) for user-facing instructions.

The updated source suite passes **424 tests**. Ruff and bytecode compilation pass;
the environment's 53 installed packages satisfy their dependency constraints.
The offline integration check loads all account dependencies and finds the
Windows Credential Manager backend. Login/provider forms, the QR approval
button, cloud inspector, and panel tools were rendered with fixture data and
inspected for layout issues.

The initial frozen build passed command-line checks but failed to load QtWidgets
when launching the GUI. Dependency inspection traced this to a third-party
`icuuc.dll` collected from the build machine's Poppler installation. Its
version-suffixed exports do not match Qt's imports of the Windows ICU C API.
Removing that copy allowed the main window to initialize. Both Windows specs
now exclude the unversioned ICU DLL names, leaving them to the OS. See
[Microsoft's Windows ICU documentation](https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-).

The current Windows builds are `dist/device-stability/LumiSync/LumiSync.exe`
and `dist/device-stability/LumiSync-Windows-x64-onefile.exe`. Both pass
`--help`, `--check-integrations`, and `--check-gui`. The GUI check constructs,
renders, and closes the actual main window and Govee/Tuya/LSC account forms with temporary empty settings,
without logging in, discovering devices or checking for updates. The Windows
release script runs all three checks for both packaging variants.
Keep the adjacent `_internal` directory with the onedir executable; the
single-file executable is standalone.
Earlier builds remain in `dist/device-layout/`, `dist/account-update/`, `dist/vendor-audit/` and
`dist/device-capabilities/` and `dist/ui-refresh/`. Existing app processes and older paths were left
untouched. Close the current app, launch the current build, then choose
**Find Devices** to refresh local discovery, linked accounts and saved readings.

Both corrected executables exit with code 0 for all three checks, and the
checks leave personal device settings and GUI preferences unchanged. The device
screen was also rendered and inspected with switch, white-light and metered-plug
fixtures. The user's later screenshots show real LSC watts, volts, amps and
monthly usage. The new polling/layout checks use isolated fixtures; physical
devices and vendor reporting intervals were not exercised by those checks.

The device-layout follow-up replaces fixed card widths with balanced, filled
columns, keeps the inventory beside the inspector whenever both panes fit,
and sizes short inspectors to their content. Metadata-based hardware glyphs
distinguish plugs, wall switches, LED strips and matrix panels. Targeted reads
coalesce by identity during another query and run ahead of broad refreshes.
Status worker results and cleanup are explicitly queued to the GUI thread,
with thread ownership retained until native cleanup finishes. Fifteen inventory
regressions include a real event-loop/background-worker metering check, hidden
polling, metadata churn, reorder/removal, keyboard access and actual geometry.

The later crash report is backed by a Windows heap-corruption event
(`0xc0000374`, 16:07:34) for the device-layout onefile. Actual Qt controls and
fake LAN I/O reproduce that fault when a status worker is released before
native cleanup joins. Cleanup now checks `wait(0)` and retries asynchronously;
discovery, Bluetooth, sync, drawing and update workers follow the same rule.
Confirmation requests and pending commands coalesce by device/control, old
reads cannot replace a newer write, and a dragging slider resists readback.
The stress subprocess passes 1,200 interactions and 74 background queries,
including correct final power/brightness. The complete suite passes **424 tests**.
Native fault traces and Qt warnings now persist in `~/.lumisync/logs`, available
from Settings → About. Physical device I/O was replaced for crash verification.
