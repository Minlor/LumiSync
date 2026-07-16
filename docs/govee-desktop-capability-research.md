# Govee Desktop Capability Research (build 2.40.50)

A second, deeper pass over `C:\Program Files\Govee\Govee Desktop` and
`%LOCALAPPDATA%\GoveeDesktop`, focused on **device compatibility**, the
**capability model**, and **multi-device sync**. This complements
[govee-lan-research-and-implementation.md](govee-lan-research-and-implementation.md),
which covered the LAN protocol basics.

Method: read-only inspection of the local install (managed .NET assemblies +
shipped PDB symbols) and the local device cache on this machine. No TLS
interception, no credential extraction. Personal identifiers (MAC, IoT topics,
Wi-Fi info, account blobs) are intentionally omitted here.

## Build Facts

- App version: **2.40.50** (was 2.40.20 in the first pass).
- Runtime: **.NET 6.0.36**, WPF desktop (`Microsoft.WindowsDesktop.App`).
- `device_config.ini` stores **encrypted** account/device blobs (`value`,
  `value2`) — AES-like base64. Not used and not reproduced.
- `device_info.ini` stores a **plaintext capability + state cache** — this is
  the useful artifact.

## Discovery: Govee Desktop Uses Three Methods

From `Govee.Application.dll` symbols:

- `GetLanDevicesByUdpAsync` — UDP multicast scan (`239.255.255.250`). This is
  what LumiSync already does.
- `GetLanDevicesByIcmpAsync` — an **ICMP ping sweep** of the local subnet.
- `GetLanDevicesByIotAsync` — cloud/IoT discovery (out of scope for LumiSync).
- `CheckLanDevicesByUdpAsync` — periodic UDP health/liveness check.

**Takeaway:** the ICMP sweep is a fallback for networks where UDP multicast is
filtered (common on segmented/guest Wi-Fi and some managed switches). Adding an
ICMP/ARP sweep that then probes each responsive host on `4001/4002` would make
LumiSync discovery meaningfully more reliable.

## LAN Command Surface (confirms + extends LumiSync)

Confirmed command shapes (consistent with what LumiSync sends):

- `colorwc` with `{ color, colorTemInKelvin }` — **exact match** to LumiSync.
  There is also a `colorTemInKelvincolor` field: the RGB that represents the
  chosen white temperature (used for UI display of tunable white).
- `razer` — the per-segment color stream LumiSync uses for monitor/music sync.
- `brightness`, `turn`, `status` / `devStatus` — as already handled.

New/adjacent channels seen (mostly IoT, kept as context):

- `multiSync` / `multisync` — **lock-step multi-device** coloring. Govee uses
  this so several strips update as one coordinated surface. Timestamped per
  device (`IotMultisyncUpdateTime`).
- `ptReal` / `ptreal` — realtime `pt` streaming (per-frame segment data over
  IoT). `iotPtrealRsp` is the ack.
- `ColorTemInKevinHelper.GetColorTemperatureFromColor` + `colorTemperatureMap`
  in `Govee.Infrastructure.Shared.dll` — a **Kelvin ↔ RGB** conversion table.

**Takeaway:** LumiSync currently hardcodes `colorTemInKelvin: 0`. A tunable-white
(color-temperature) control is a straightforward feature-parity add using the
per-SKU `colorTemperatureStart/End` range and a Kelvin→RGB curve.

## Capability Model (the big compatibility win)

`Govee.Application.Contracts.dll` defines a per-SKU capability DTO. Full field
set observed:

```
segmentNums            colorTemperatureStart   supportRazer
skuType                colorTemperatureEnd     supportFeast
colorModeType          deviceAreaType          supportMusicalFeast
colorModeSegment       pactType                supportDiy
colorModeIotType       pactCode                supportScene
supportColor           supportSpeed            supportSceneFeast
supportBrilliant       supportSnapshot         supportBleVersions
supportWifiVersions    supportSkus             supportBleVersionSoft
```

Concrete example from this machine's cache (SKU `H619C`, "10m RGBIC Pro Strip"):

```jsonc
{
  "sku": "H619C",
  "segmentNums": 10,           // UI zones the app exposes for mapping
  "supportRazer": true,        // per-segment LAN color stream (LumiSync path)
  "supportMusicalFeast": 1,    // music mode
  "supportColor": 1,
  "colorTemperatureStart": 2000,
  "colorTemperatureEnd": 9000, // tunable white range in Kelvin
  "colorModeType": 1, "colorModeSegment": 0, "colorModeIotType": 1,
  "pactType": 0, "pactCode": 0 // protocol family/version selector
}
```

And the per-device record adds live capability/state:

```jsonc
{
  "SkuId": "H619C",
  "SegmentNums": 10,           // UI segments
  "ColorIc": 40, "ShapesNum": 40, // physical addressable ICs (4 ICs per segment)
  "IsSupportedPC": true, "IsSupportedLAN": true, "IsSupportedBlue": true,
  "PactType": 0, "PactCode": 0
}
```

Two key insights:

1. **Per-SKU segment count is knowable.** The first research pass concluded the
   LAN scan does not return a reliable zone count — true, but the count is
   available from SKU metadata. LumiSync can ship a **bundled SKU→capability
   table** (or import the user's own `device_info.ini`) and auto-configure the
   zone count from the discovered `sku`, instead of relying on a manual
   override. `H619C = 10`.
2. **UI segments vs physical ICs.** `SegmentNums` (10) is the mapping
   granularity; `ColorIc`/`ShapesNum` (40) is the physical LED-IC count. The
   `razer` stream is sent per UI segment; the firmware fans each segment across
   its ICs. LumiSync's current per-segment model is correct; higher-resolution
   sync would mean sending up to `ColorIc` colors.

## H6672 Boundary (verified 2026-07-15)

Govee Desktop 2.40.50's production capability response deliberately excludes
H6672 ("RGBWIC TV Backlight") from its PC DreamView/Razer UI:

```jsonc
{
  "sku": "H6672",
  "segmentNums": 0,
  "supportRazer": false,
  "supportFeast": false,
  "colorModeSegment": 0,
  "colorModeIotType": 1,
  "supportWifiVersions": [{ "wifiVersionHard": "1.07.00" }],
  "supportBleVersions": [{ "bleVersionHard": "3.08.01" }]
}
```

Those fields are Desktop eligibility gates, not proof that the firmware rejects
all LAN segment frames. Issue #31's physical-device tests show partial B1/B0
acceptance, so LumiSync currently treats that route as experimental. The
14-section count is independently confirmed by Govee Home 7.5.20's
`ColorPieceConfig`: H6672 goods type 332 resolves to `min(device.ic, 14)`, and
the bundled H6672 metadata has `ic=56`. This is the manual-color-piece count,
not an official Desktop B0 count; the per-device override must still win.

The decompiled Desktop encoder confirms the generic B0 stream is RGB-only:

```text
BB lenHi lenLo B0 gradientFlag count (R G B)*count XOR
```

`length = 2 + 3*count`. There is no white byte. The separate B4 encoder is
hard-coded to H6608/H6609 variants, and its fourth record byte is an IC/run
repeat count rather than a white channel.

Govee Home 7.5.20 independently reveals H6672's official manual-segment route.
It uses BLE service `00010203-0405-0607-0809-0a0b0c0d1910`, write
characteristic `00010203-0405-0607-0809-0a0b0c0d2b11`, or sends the same raw
controller frames through authenticated IoT `ptReal`. Desktop's type-1 color
record has this 20-byte RGB + segment-mask shape:

```text
33 05 15 01 R G B 00 00 00 00 00 mask0 mask1 mask2 mask3 00 00 00 XOR
```

Desktop sends H6672 `ptReal` through TLS MQTT, and the inspected mobile path is
BLE or cloud; neither app provided evidence that this command is accepted as a
LAN UDP/4003 message. It is also one masked-color operation rather than a proven
high-frame-rate 14-color stream. Therefore LumiSync should not silently replace
its local B0 experiment with cloud credentials, or append a speculative W byte.
A sanitized physical H6672 BLE capture is the next decisive artifact if B0
cannot be made stable.

## Multi-Device Sync Model

`DreamviewList` (monitor), `RazerList` (music/razer switch), and `MusicList`
(music feast) all share the same shape: **one saved mode instance that fans out
to a `devices[]` array**, each device carrying its own brightness and segment
mapping.

Monitor (DreamView) record, redacted:

```jsonc
{
  "feastId": "67", "name": "...", "brightness": 10,
  "saturation": 70,          // <-- validates LumiSync's new saturation control
  "sensitivity": 50, "on": 0,
  "devices": [{
    "sku": "H619C", "brightness": 0,
    "segments": [ { "segmentIndex": 1, "areaIndex": 4, "color": "#FF00FF00" }, ... ]
  }]
}
```

Music (feast) record, redacted:

```jsonc
{
  "feastId": "1447", "name": "Moosic",
  "lightness": 83, "unifiedLightness": 0, "sensitivity": 50,
  "musicFeastConfigId": 3,   // which visualization algorithm
  "colors": "#FFFF0000,#FFFFA500,#FFFFFF00,#FF008000,...",  // palette
  "recentUseColors": [...], "baseColor": "#FFFFFFFF",
  "devices": [{ "sku": "H619C" }]
}
```

**Takeaways for LumiSync:**

- The `devices[]` fan-out with **per-device brightness + per-device segment
  mapping** is exactly the shape LumiSync's multi-device sync should take. The
  engine already fans one sync to many devices; formalizing per-device
  brightness and per-device LED mapping (plus saved "groups") completes it and
  fills the "Sync Groups" placeholder already stubbed in Settings.
- `segmentIndex → areaIndex → color` confirms LumiSync's screen-region-to-
  segment mapping is the right abstraction (Govee uses a grid `areaIndex`;
  LumiSync uses normalized rectangles, which is finer-grained).
- The music record having a **color palette + sensitivity + baseColor** directly
  validates the palette/sensitivity controls just added to LumiSync.

## Third-Party Pipe API (unchanged conclusion)

`GoveeAPI/GoveeAPI.dll` (`GoveePipe` / `GoveeDesktopPipe`) exposes:

```
GetDeviceBaseInfo / GetDeviceBaseInfoByName
DeviceSwitchControl / DeviceBrightnessControl / DeviceColorControl
DeviceRZSwitchControl / DeviceSegmentsColor(sku, colorInfo, isGradientOff)
SendThirdNewRazerColor   // a newer razer segment-color format
```

`SendThirdNewRazerColor` resolves to the B4 IC/run encoder used by H6608/H6609
variants. Its fourth record byte is controller metadata, not RGBW. The pipe
stays gated behind "Enable API" + per-device LAN API, so it remains out of
LumiSync's core path.

## Prioritized Improvement Backlog

Highest compatibility/UX value first:

1. **Bundled SKU capability table + auto zone-count.** Map discovered `sku` →
   `segmentNums` and capability flags so devices "just work" without a manual
   zone override. Optionally import the user's own `device_info.ini` when Govee
   Desktop is installed (real data, no guessing). Enables trustworthy
   multi-device sync.
2. **Formal multi-device groups** with per-device brightness and per-device LED
   mapping (the `devices[]` model). Wire the existing Settings "Sync Groups"
   placeholder to real saved groups.
3. **ICMP/ARP discovery fallback** for multicast-hostile networks
   (`GetLanDevicesByIcmpAsync` equivalent).
4. **Tunable-white / color-temperature control** using `colorTemInKelvin` +
   per-SKU `colorTemperatureStart/End` and a Kelvin→RGB curve.
5. Capability-gated UI: hide Razer/Music/Color controls a device doesn't support
   (`supportRazer`, `supportMusicalFeast`, `supportColor`), while preserving an
   explicit per-device experimental override for devices such as H6672.

Explicitly still out of scope: cloud login, MQTT, `multiSync`/`ptReal` IoT
channels, scenes/DIY. LAN-first remains the product boundary.

## Toward Multi-Vendor (Govee BLE, iDotMatrix, …)

The capability model above is a good template for a **device-abstraction layer**
so LumiSync is not Govee-only. `IsSupportedLAN` / `IsSupportedBlue` and the
per-SKU capability flags show Govee itself treats transport (LAN/BLE) and
capability (razer/music/color/segments) as orthogonal axes.

Recommended shape for the future:

- A `Device` carries identity + capabilities + a **transport adapter**.
- A transport adapter interface (`discover`, `set_power`, `set_brightness`,
  `set_color`, `set_segments`, `query_status`) with implementations for
  Govee-LAN (today), and later Govee-BLE and iDotMatrix-BLE.
- The sync engine already emits a list of per-segment colors per frame; it
  should target the adapter interface, not `connection.send_razer_data`
  directly, so any device family can receive the same color stream.

This keeps the fast local color pipeline (now vectorized + smoothed) and lets a
BLE device (e.g. the iDotMatrix pixel display) plug in as just another adapter.
