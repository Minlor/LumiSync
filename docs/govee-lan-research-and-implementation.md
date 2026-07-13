# Govee LAN Research And Implementation Plan

This document summarizes the local Govee Desktop investigation done on April 29, 2026 and turns it into an implementation plan for LumiSync.

The goal is interoperability with devices on the user's own LAN. Do not depend on Govee cloud credentials or private app secrets for normal LumiSync operation.

## What We Confirmed

Govee Desktop is a .NET/WPF app with these relevant assemblies:

- `GoveeDesktop.dll`
- `Govee.Application.dll`
- `Govee.Infrastructure.Shared.dll`
- `Govee.Service.dll`
- `GoveeAPI/GoveeAPI.dll`

Static analysis found:

- App version: `2.40.20`
- Main web host in constants: `https://desktop.govee.com`
- Runtime API host observed in logs/connections: `https://app2.govee.com`
- Other hosts: `https://gapp.govee.com`, `https://lambda-api.igovee.com`, Sentry, Google Analytics
- MQTT-over-TLS is used on port `8883` via `M2Mqtt.dll`
- LAN UDP constants:
  - multicast address: `239.255.255.250`
  - multicast send port: `4001`
  - PC receive/listen port: `4002`
  - device control port: `4003`

Runtime capture confirmed direct LAN control is plain JSON over UDP.

## Runtime Capture Evidence

Capture artifacts:

- `govee-lan.pcapng`
- `govee-lan-capture.log`
- `govee-ui-capture.pcapng`
- `govee-ui-capture.log`

Capture summary:

- 16 UDP packets captured.
- Govee Desktop sent repeated status probes from the PC to the strip.
- The strip replied with status JSON.

Observed flow:

```text
PC 192.168.0.3:4002 -> device 192.168.0.10:4003
device 192.168.0.10:<ephemeral> -> PC 192.168.0.3:4002
```

Important: for the tested `H619C` device, sending from source port `4002` matters. A probe sent from a random source port timed out. The same probe sent from source port `4002` received a reply immediately.

## Confirmed Packet Shapes

### Status Request

```json
{"msg":{"cmd":"status","data":{}}}
```

### Status Response

```json
{"msg":{"cmd":"status","data":{"onOff":0,"brightness":100,"pt":"uwABsgAI"}}}
```

Notes:

- `onOff` is integer-like: `0` off, `1` on.
- `brightness` is `0..100`.
- `pt` is a base64-ish protocol payload. In the captured off state it was `uwABsgAI`.
- This tested device returned `cmd: "status"`, not `cmd: "devStatus"`.

### Brightness Control

```json
{"msg":{"cmd":"brightness","data":{"value":100}}}
```

Observed behavior:

- No immediate acknowledgement was observed.
- A follow-up status query confirmed the command applied.
- Setting brightness to `100` also changed `onOff` from `0` to `1` on the tested device.

### Power Control

```json
{"msg":{"cmd":"turn","data":{"value":0}}}
```

Observed behavior:

- No immediate acknowledgement was observed.
- A follow-up status query confirmed `onOff` returned to `0`.

### Existing Color Control Shape

LumiSync already sends this:

```json
{"msg":{"cmd":"colorwc","data":{"color":{"r":255,"g":0,"b":0},"colorTemInKelvin":0}}}
```

Static Govee Desktop analysis also found `colorwc`, so this command name is consistent with Govee Desktop.

## Slower UI Exercise Findings

We ran a slower desktop automation pass through these views:

- Device List
- Color / Scene / My DIY / Snapshot tabs
- Movie-Watching/Gaming DreamView
- Music Dreamview
- Razer
- Tap-to-Run
- Community

The slower pass matters because some mode pages need a few seconds to render and initialize after the nav click.

What we saw in the capture:

- HTTPS traffic to `34.232.112.160:443`, which resolves to `app2.govee.com`
- Persistent MQTT-over-TLS traffic to `54.87.163.43:8883`
- Local UDP traffic to the device on `192.168.0.10:4003`

### Confirmed Local UDP From The Device List Brightness Slider

During the Device List brightness slider interaction, Govee Desktop emitted repeated local UDP commands:

```json
{"msg":{"cmd":"brightness","data":{"value":28}}}
{"msg":{"cmd":"brightness","data":{"value":86}}}
```

Important nuance:

- These brightness commands were sent from ephemeral local source ports in the capture, not from `4002`.
- Earlier status probing still used `4002`.
- That suggests status polling and direct control may have different source-port requirements.

### What We Did Not See Locally

During the slower pass, we did not capture new local UDP payloads for:

- the Device List power toggle
- DreamView switch/brightness actions
- Music Dreamview switch/brightness actions
- Razer switch/brightness actions
- Tap-to-Run nav
- Community nav

That does not prove those actions never use LAN control, but in this run they did not surface as plain UDP commands to port `4003`.

Most likely interpretation:

- basic per-device brightness is definitely available as direct LAN UDP
- advanced modes are at least coordinated through the app's cloud and/or MQTT channels

That said, the current Govee error log adds an important nuance:

- `2026-04-29.log` contains `DreamviewService.ColoringAsync` failures with `HRESULT 0x80070005 / E_ACCESSDENIED` while reporting the active screen resolution.

That strongly suggests DreamView also has a local desktop-processing component, likely involving screen capture or screen-color sampling, even if the mode definition and saved configuration come from cloud-backed data.

## Practical Takeaway

For LumiSync's own implementation, this strengthens the priority split:

- local UDP first for status, power, brightness, color, and direct strip control
- cloud/MQTT findings remain historical context only; they are not part of LumiSync's runtime control path

The later static string pass added two useful product-boundary clues from Govee Desktop itself:

- the UI text says Movie-Watching/Gaming DreamView colors come from the selected screen and that DreamView is only available under LAN
- the UI text says DIY, Scene, and Snapshot content comes from Govee Home App saved data and requires login for the richer experience

That maps neatly to LumiSync's direction: monitor sync, music sync, status, and manual control should stay native/local.

## Cloud And MQTT Findings

Govee Desktop opens:

- HTTPS connections to the Govee app API pool.
- A persistent MQTT-over-TLS connection to an AWS-like endpoint on `8883`.

Useful REST routes found statically:

```text
/bff-app/v1/pc/login
/bff-app/v1/pc/logout
/bff-app/v1/tokens/refresh
/bff-app/v1/pc/codes/verification
/bff-app/pad/v1/qrcode/generate
/bff-app/pad/v1/qrcode/status
/app/v1/account/iot/key
/bff-app/v1/pc/user/devices
/bff-app/pc/v1/support-skus
/bff-app/v1/pc/iot-control
/bff-app/v1/fx-device/iot-msgs
/bff-app/v1/pc/devices/groups
/bff-app/v1/pc/devices/scenes
/bff-app/v1/pc/devices/diy
/bff-app/v1/pc/snapshots
/bff-app/v1/pc/device-list/light-eff
/bff-app/v1/pc/razer
/bff-app/v1/pc/razer/sync
/bff-app/v1/pc/feasts
/bff-app/v1/pc/feast/sync
/bff-app/v1/pc/music/feasts
/bff-app/v1/pc/oneclick
/bff-app/v1/pc/oneclick/control
```

### Current Error Log Path

The current desktop build is writing error logs under:

```text
%LOCALAPPDATA%\GoveeDesktop\log\error
```

Those logs are useful because failed requests often include:

- `URL`
- `Header`
- `QueryParameter`
- `Body`
- the internal service/class name that made the request

That gives us a low-risk way to learn request shapes without needing to intercept TLS or scrape secrets from memory.

### Failed Request Shapes Confirm More Of The Cloud Stack

Historical failure logs confirm these startup and account flows:

- `GET /bff-app/v1/pc/check-version`
- `GET /bff-app/v1/pc/users`
- `GET /app/v1/account/iot/key`
- `GET /bff-app/v1/pc/settings`
- `GET https://gapp.govee.com/bff-gapp/alarm/v1/service-failure/notices?location=1`
- `POST /bff-app/v1/pc/exception-report-log`

They also show richer request headers than we first saw, including:

- `Authorization`
- `clientid`
- `clientType`
- `AcceptLanguage`
- `pcversion`
- `model`
- `clientName`
- `sysVersion`
- `appVersion`
- `timezone`
- `country`

The internal class names in those errors line up with the architecture we inferred:

- `SettingService.CheckVersionAsync`
- `SettingService.GetServerSettingsAsync`
- `UserService.GetUserBaseInfo`
- `UserService.GetUserAccountKey`
- `DeviceService.CheckLanDevicesByUdpAsync`
- `AwsIotHelper.Connect`

Taken together, that suggests the startup sequence is roughly:

1. check app version and desktop settings
2. fetch user/account context
3. fetch IoT key material via `/app/v1/account/iot/key`
4. establish MQTT/TLS connectivity
5. keep LAN and IoT status in parallel where possible

### Local Cache Tells Us How Advanced Modes Are Stored

`%LOCALAPPDATA%\GoveeDesktop\config\device_info.ini` is especially informative. It caches multiple layers of state:

- `Device`
- `Sku`
- `DreamviewList`
- `RazerList`
- `MusicList`
- card/page layout info for the desktop UI

Useful capability and state fields observed in that cache include:

- `IsSupportedPC`
- `IsSupportedLAN`
- `IotTopic`
- `AccountTopic`
- `IsLanOn`
- `IsUdpOnline`
- `IsIotOnline`
- `UdpStatusUpdateTime`
- `IotStatusUpdateTime`
- `IotPtrealUpdateTime`
- `IotPtiotopUpdateTime`
- `IotMultisyncUpdateTime`
- `LanIpByIot`

The cached SKU metadata also advertises feature support per model, including fields such as:

- `supportRazer`
- `supportFeast`
- `supportDiy`
- `supportScene`
- `supportSnapshot`
- `supportMusicalFeast`
- `colorModeIotType`
- `supportSpeed`

Note: later checks against the public LAN/API surfaces did not provide a
reliable per-device zone/segment count. LumiSync should not try to infer that
value from `scan` or status replies; it uses a user-configured zone override
when the default count is wrong.

The advanced-mode sections are even more revealing:

- `DreamviewList` stores a saved feast/dreamview id, brightness/saturation/sensitivity, and per-segment area mappings with ARGB-like color values.
- `RazerList` stores a saved razer preset id, on/off state, brightness, and attached devices.
- `MusicList` stores a saved music-feast id, palette, sensitivity, unified-lightness flags, and attached devices.

That strongly supports the idea that Govee Desktop is not inventing these modes locally from scratch. Instead, it appears to combine:

- direct LAN control for device-level commands
- cached local metadata for fast UI restore
- cloud/MQTT-backed identifiers and saved mode definitions for DreamView, Music, Razer, and Tap-to-Run style experiences

### Symbol Clues For Advanced-Mode Workflows

The bundled symbols and string tables also reveal a surprising amount about how the editor flows are structured on the desktop side.

Examples seen in `GoveeDesktop.pdb` and the contracts string surface include:

- `Govee.Presentation.WPF.Views.Dreamview.DreamviewControlAreaDrawer`
- `LoadDreamviewDevicesAsync`
- `SetDeviceAutoSegmentsAsync`
- `ShowTemplateList`
- `Govee.Presentation.WPF.Views.Music.MusicDreamviewEditDrawer`
- `LoadMusicDreamviewLightings`
- `LoadMusicDreamviewDevices`
- `LoadMusicDreamviewColor`
- `LoadMusicDreamviewRecentColors`
- `LoadMusicDreamviewBaseColors`
- `Govee.Presentation.WPF.Views.TapToRunPage`
- `AddTapToRunGroup`
- `Govee.Presentation.WPF.Views.Scene.SceneEditDrawer`
- `LoadScene`
- `LoadSceneSceneTypes`
- `LoadSceneVoices`

This does not give us decrypted payloads, but it does reinforce the feature split:

- DreamView has a real local area/segment editor and local coloring pipeline.
- Music Dreamview has local palette/base-color/recent-color editing on top of saved mode records.
- Tap-to-Run and Scene flows look more like saved orchestration/grouping features than raw device commands.

For LumiSync, that makes the product boundary clearer:

- reproducing direct strip/device control is straightforward and already proven
- reproducing DreamView-style monitor mapping is a separate local feature project
- reproducing Govee's saved cloud modes would require additional cloud/MQTT reverse engineering beyond what passive inspection can currently reveal

For LumiSync, prefer the local UDP path first. Cloud routes are useful historical context for understanding Govee Desktop, but they are outside the runtime product boundary for basic local control.

## Named Pipe API Findings

Govee ships a third-party API wrapper:

- `GoveeAPI/GoveeAPI.dll`
- File description: `ThirdInterface`
- Named pipe strings: `GoveePipe`, `GoveeDesktopPipe`

Static method surface:

```text
ConnectGovee.GetDeviceBaseInfo()
ConnectGovee.GetDeviceBaseInfoByName(skuName)
ConnectGovee.DeviceSwitchControl(skuName, isOff)
ConnectGovee.DeviceBrightnessControl(skuName, brightness)
ConnectGovee.DeviceColorControl(skuName, r, g, b)
ConnectGovee.DeviceSegmentsColor(skuName, colorInfo, isGradientOff)
ConnectGovee.DeviceRZSwitchControl(skuName, isOff)
```

The pipe was not active during our run. Govee Desktop UI strings show it is gated behind an `Enable API` setting, and the device itself must have LAN API enabled in the mobile app.

Conclusion: do not block LumiSync on the named pipe API. Direct UDP is simpler and already proven.

## Current LumiSync Gaps

The existing `lumisync/connection.py` already has most command writers:

- `switch(...)` sends `turn`
- `set_brightness(...)` sends `brightness`
- `set_color(...)` sends `colorwc`
- `send_razer_data(...)` sends `razer`

Main gap:

- `query_status(...)` currently sends `devStatus`.
- `parse_status_payload(...)` currently accepts only `cmd == "devStatus"`.
- The captured device responds to `status`, so status refresh can fail even though the device is reachable.

Implemented locally after this research:

- `parse_status_payload(...)` now accepts both `status` and `devStatus`.
- `query_status(...)` now tries `status` first, then `devStatus` as a compatibility fallback.
- `DeviceStatusWorker` no longer silently falls back to an ephemeral source port when `4002` is busy.
- LAN sockets are now created through one helper so discovery, status, manual controls, and sync share the same port-conflict message.
- Manual GUI power, brightness, RGB, and Razer mode controls stay LAN-only and update optimistic UI before delayed status confirmation.
- Monitor and music sync use each device's configured zone override when present, with a default of `10`.
- Cloud runtime code, API-key settings, and official Developer API fallback paths have been removed from LumiSync.

Port caveat:

- Govee Desktop and LumiSync both want local UDP port `4002`.
- Only one process can normally bind `0.0.0.0:4002`.
- If Govee Desktop is running, LumiSync discovery/status may fail because it also needs UDP source port `4002`.

Current code review notes:

- Manual control actions in the GUI already do the right broad thing: they send the command, update optimistic UI state, then schedule a delayed status refresh.
- `DeviceCard` already preserves the last chosen color if a later status payload omits RGB data.
- `DeviceStatusWorker` currently falls back to an ephemeral local port when `4002` is occupied. This looked reasonable in theory, but our live test against `H619C` suggests it is not reliable for this firmware because the device answered only when the source port was `4002`.

## What Makes Sense To Implement Now

These are the highest-value changes based on both the reverse-engineering work and the current LumiSync code.

### 1. Fix Status Compatibility First

This is the clearest bug and the best return on effort.

- Accept both `status` and `devStatus` in `parse_status_payload(...)`.
- Have `query_status(...)` send `status` first, or try `status` then `devStatus`.
- Update docstrings and any UI messaging so they do not describe `devStatus` as the only reply shape.

Why this matters:

- Status refresh drives the GUI's online/offline state.
- Manual controls already rely on delayed refresh to confirm the device state.
- If refresh stays broken, the UI will feel flaky even when commands are actually working.

### 2. Treat Port `4002` As A Hard Requirement For Now

Until we prove broader firmware behavior, assume status and LAN control need local port `4002`.

- Keep the main control/status socket bound to `4002`.
- Replace silent fallback-to-ephemeral behavior with a clear, user-facing error.
- Tell the user to close Govee Desktop or any other process using the port.

Why this matters:

- The current fallback may cause false "device offline" reports even when the device is healthy.
- A clear failure is much better than a misleading one here.

### 3. Keep Direct UDP As The Primary Integration Path

This is already the best architectural call.

- Keep discovery, status, power, brightness, color, and Razer/multi-zone on LAN UDP.
- Do not wire LumiSync to Govee cloud login, MQTT auth, or the named-pipe desktop API for core features.

Why this matters:

- The local path is proven.
- It avoids account coupling and app-secret fragility.
- It matches LumiSync's value proposition: low-latency local control.

## Good To Improve Soon

These are worth doing after the status/port fixes.

### 4. Add Parser And Socket-Failure Tests

- Unit tests for `status` and `devStatus` payloads.
- Tests for invalid JSON and unrelated command payloads.
- Tests around port bind failure behavior, especially on `4002`.

### 5. Improve Port-Conflict Messaging In Discovery And GUI Control Paths

- Discovery should fail with a specific message when `4002` is busy.
- GUI control paths should surface the same message instead of a raw socket exception.
- On Windows, optionally identify the owning PID in a future pass.

### 6. Validate Color Status Reporting Per Device Family

- Confirm whether some models return RGB in status and some do not.
- If RGB is absent, keep the current optimistic-color UI approach.
- Avoid trying to "derive" device color from `pt` until we have evidence.

## Not Worth Prioritizing Yet

These are real topics, but they should wait.

### Named Pipe API

Interesting for interoperability, but not needed for LumiSync's core product path.

### Cloud And MQTT Control

Useful for understanding Govee Desktop, but the added complexity is not justified while LAN UDP already works.

### Deep `pt` / Razer Reverse Engineering

Worth revisiting once core status, brightness, power, and color are dependable. The current `razer` path already exists and should stay stable unless we have a specific bug to solve.

## Recommended Implementation Plan

### 1. Fix Status Query Compatibility

Update `query_status(...)` to use `status` first:

```json
{"msg":{"cmd":"status","data":{}}}
```

Update `parse_status_payload(...)` to accept both:

- `status`
- `devStatus`

This keeps compatibility with any existing devices that answer `devStatus`.

### 2. Make Source Port Behavior Explicit

Keep binding the main socket to `CONNECTION.default.listen_port`, which should be `4002`.

Add clearer error handling when port `4002` is occupied:

- Detect `OSError: [WinError 10048]` / address-in-use on bind.
- Show a user-facing message:
  - "UDP port 4002 is already in use. Close Govee Desktop or another LumiSync instance, then retry discovery."
- Optionally identify the owning process on Windows via `netstat -ano -p udp`.

Do not silently fall back to an ephemeral source port for status refresh until we prove that behavior works across real devices.

### 3. Confirm Device Model Defaults

For discovered devices, make sure:

- `ip` is the LAN IP.
- `port` defaults to `4003`.
- `mac` and `model` are optional for manual devices, but useful for display and caching.

Manual add should allow:

- IP address
- model/SKU
- device id/MAC optional
- port default `4003`

### 4. Status Refresh Behavior

After commands that do not ack, refresh status:

- `turn`
- `brightness`
- `colorwc`
- `razer`

Use a short retry loop:

- Send command.
- Sleep `150-300 ms`.
- Query status up to 2 or 3 times.

For high-frequency sync modes, do not query after every frame. Query only on start/stop/manual controls.

### 5. Validate Color Commands

Known command:

```json
{"msg":{"cmd":"colorwc","data":{"color":{"r":R,"g":G,"b":B},"colorTemInKelvin":0}}}
```

Need validation:

- Send a harmless known color.
- Query status.
- Confirm whether the response includes `color`.
- If `status` does not report RGB, rely on optimistic UI state for color while keeping `onOff` and `brightness` from device status.

### 6. Preserve Existing Razer Payload Path

Static analysis confirms Govee Desktop uses `razer` for segment/multi-zone data. Keep existing `utils.convert_colors(...)` and `send_razer_data(...)`.

Known payloads from static analysis:

- Razer on: `uwABsQEK`
- Razer off: `uwABsgEJ`
- Captured status/off `pt`: `uwABsgAI`

Do not overfit these until more captures are available.

### 7. Add Focused Tests

Unit-test parser behavior:

- `status` response with `onOff`, `brightness`, `pt`
- `devStatus` response with `onOff`, `brightness`, `color`, `colorTemInKelvin`
- invalid JSON
- unrelated command

Integration/manual smoke tests:

- bind to `4002`
- send `status`
- send `turn` and verify status
- send `brightness` and verify status
- send `colorwc` and verify visible device change

## Proposed Code Changes

High-priority edits:

- `lumisync/connection.py`
  - Change `query_status` command from `devStatus` to `status`, or try both.
  - Change `parse_status_payload` to accept both commands.
  - Update docstrings to mention status variants.
  - Improve port-in-use handling in `connect`.
  - Route LAN sockets through one shared helper and avoid ephemeral fallback ports for status polling.

Medium-priority edits:

- `lumisync/gui/controllers/device_controller.py`
  - Surface port-in-use errors cleanly.
  - Keep manual control LAN-only, with optimistic UI and delayed status confirmation.

Later:

- Add optional packet capture/debug tool docs for maintainers.
- Add a "Govee Desktop is running" warning if UDP `4002` is unavailable.

## Open Questions

- Does every supported SKU answer `status`, or do some only answer `devStatus`?
- Does every device require source port `4002`, or only this `H619C` firmware?
- Does `colorwc` status response include RGB on some models?
- What is the exact `razer`/segment payload format beyond the current `utils.convert_colors(...)` implementation?
- Does multicast discovery still need both `239.255.255.250:4001` and broadcast `255.255.255.255:4001`, or can we narrow it?

## Implementation Order

1. Fix status command parsing and querying.
2. Add parser tests.
3. Improve port `4002` bind error handling.
4. Validate color command status behavior.
5. Add GUI messaging for occupied port / Govee Desktop conflict.
6. Revisit Razer/segment payloads only after the core LAN path is reliable.

## Sync Engine Performance And Quality (Implemented)

The reverse-engineering above confirmed that Govee Desktop's DreamView does its
own local screen sampling and coloring. That framed a second work item: bring
LumiSync's own local sync engine up to a comparable quality/latency bar without
any cloud dependency. The following is now implemented.

Shared pipeline lives in `lumisync/sync/processing.py` and
`lumisync/sync/audio.py`, used by both the CLI (`sync/monitor.py`,
`sync/music.py`) and the GUI workers (`gui/controllers/sync_controller.py`).

### Monitor Sync

- **One packet per frame instead of ten.** The old path sent ten interpolated
  Razer packets per captured frame with a `10 ms` sleep between each, hard-capping
  the effective rate near 10 FPS, adding ~`100 ms` of built-in latency, and
  flooding the strip's packet queue. Smoothing now happens on the color value via
  a temporal EMA (`ColorSmoother`), so each frame emits a single packet.
- **Vectorized region averaging.** Instead of reading one center pixel per zone
  through PIL `getpixel`, zones are averaged over their full rectangle with
  NumPy directly on the captured array (`ScreenGrab.capture_array`). This is both
  faster and far less noisy.
- **Gamma-correct sampling** (average in linear light) plus an optional
  **saturation boost** so mixed regions do not turn muddy and ambient color pops
  closer to DreamView.
- **Frame pacing + delta-skip.** A target FPS caps CPU, and identical frames are
  not retransmitted, so a static screen stops generating UDP traffic.

### Music Sync

- **FFT frequency bands instead of raw volume.** The old path keyed color off
  peak amplitude alone (quiet=red, louder=green, loudest=blue). It now runs an
  rFFT per window and splits bass / mid / treble energy, mapping the spectral
  balance to color (bass→red, treble→blue) with loudness driving intensity.
- **Recorder opened once per connection**, not once per frame, and frames are
  paced.

### Tunables

All of the above are configurable in `config/options.py` under the `SYNC`
namespace (`monitor_fps`, `smoothing`, `gamma_correct`, `saturation`,
`delta_threshold`, `music_fps`, `music_gain`, `music_smoothing`) and the
`AUDIO.music_window` FFT window length.

### Tests

- `tests/test_processing.py` — zone averaging, gamma correctness, saturation,
  brightness, delta detection, EMA smoother.
- `tests/test_audio.py` — band separation for bass/mid/treble tones, silence
  handling, stereo downmix, and color mapping.
