# iDotMatrix BLE Research + Integration Plan

First pass at supporting **iDotMatrix** pixel-matrix displays as a second device
family in LumiSync, from the official app `com.tech.idotmatrix` v2.1.2
(`iDotMatrix_2.1.2_APKPure.xapk`).

Method: read-only static inspection of the APK (unzipped DEX string tables). No
dynamic instrumentation. Exact command opcodes should be confirmed against the
community library and/or a BLE capture before shipping (see Caveats).

## What the App Is

- Native Android app (Java/Kotlin) — 4 `classes*.dex`, **no** native `.so`, not
  Flutter/Unity. So the whole protocol lives in the DEX.
- Package `com.tech.idotmatrix`. Relevant classes seen:
  `bean/DiyData`, `bean/SendData`, `bean/TextData`, `bean/ModeBean`,
  `bean/Point`, `util/LedUtil`, `view/LedView`, `view/GIFView`.
- The device is a **pixel matrix** with on-device modes (clock, countdown,
  chronograph/stopwatch, scoreboard, DIY draw, image/GIF, and a rhythm/music
  animation).

## Confirmed BLE Transport

GATT UUIDs found in the DEX:

| UUID | Role |
| --- | --- |
| `000000fa-0000-1000-8000-00805f9b34fb` | primary control **service** |
| `0000fa02-0000-1000-8000-00805f9b34fb` | **write** characteristic (commands) |
| `0000fa03-0000-1000-8000-00805f9b34fb` | notify characteristic (responses) |
| `0000fee9-0000-1000-8000-00805f9b34fb` | Telink **OTA** (firmware; not control) |
| `0000ae00/ae01/ae02-...` | secondary UART used by some models |
| `00001101 / 0000110b / 0000111e` | Bluetooth **Classic** SPP/A2DP (speaker models) |

Control path for LumiSync: connect GATT, write length-prefixed command packets
to `0000fa02`, optionally subscribe to `0000fa03` for acks.

## Matrix Sizes

Asset names confirm these resolutions (as `cols x rows`):

- `16x16`, `16x32`, `16x64`, `32x32`

`32x32` is a good default. These map to `segment_count = cols * rows` in
LumiSync's driver capabilities (e.g. 32x32 = 1024 addressable pixels).

## Command Framing (CONFIRMED via decompilation)

Decompiled `com.tech.idotmatrix` (jadx) — the command builders live in
`com.tech.idotmatrix.ble.BleProtocolN` and `ble/send/{BaseSend,SendCore}`, and
writes go through `com.heaton.baselib.ble.BleManager.writeAll` → characteristic
`0000fa02`.

Every command is a little-endian **length-prefixed** byte array
(`[len & 0xFF, len >> 8, <body>]`). Confirmed commands LumiSync uses:

| Action | App method | Bytes |
| --- | --- | --- |
| Brightness (0-100) | `setLight` | `[5,0,4,0x80,val]` |
| Power on/off | `sendSwitchplate` | `[5,0,7,1,on]` |
| Solid color | `sendColor` | `[7,0,2,2,r,g,b]` |
| Enter DIY mode | `enterDiy` | `[5,0,4,1,mode]` |

Two critical transport details (the reason an early attempt connected but did
nothing):

1. **Writes are WITH response.** The app never calls `setWriteType`, so Android's
   default write-with-response is used. The panel ignores no-response writes.
2. The app **subscribes to notifications** on `d44bc439-abfd-45a2-b575-925416129602`
   right after connecting (`BleManager` `UUID_READ_VER_CHA`).

For real-time **sync**, LumiSync drives the panel as **one ambient light**: it
averages each frame to a single color and sends `sendColor` (per-pixel DIY frames
are too many BLE packets for high FPS).

For **drawing / animation**, the DIY pixel path is now implemented. From
`SendCore.payload` (type 5) + `BleProtocolN.enterDiy`:

- Enter/quit DIY mode: `[5,0,4,1,mode]` — mode 1 = enter+clear, 2 = quit-keep,
  3 = enter-no-clear (`DiyImageFun`).
- Draw one color group: `[len_lo,len_hi, 5, 1, moveType, r,g,b, x0,y0, x1,y1, ...]`
  — every `(x,y)` painted with that color; `moveType` 0 = static (`DiyImageMoveType`).
  Coordinates are `(column,row)` bytes, matching the app's single-pixel form
  `[r,g,b,column,row]`.

LumiSync encodes a full grid by grouping pixels by color (`build_diy_frames`),
wraps them in enter/quit, and `IDotMatrixBleAdapter.draw_grid()` / `show_image()`
/ `play_animation()` push them. The **Draw** tab is a pixel canvas (color picker,
clear/fill, per-size grid, add-frame + play-animation) that sends to the selected
Bluetooth device. Golden-byte tests pin the encoding.

## Integration Plan

1. **Driver + our own encoder — done.** `lumisync/drivers/idotmatrix_ble.py`
   holds the confirmed UUIDs/sizes and LumiSync's **own** protocol encoder
   (`frame()`, `build_brightness_frame()`, `build_power_frame()`,
   `build_pixel_frame()`, `pack_pixels()`, `chunk()`, `screen_to_pixels()`).
   These are pure and unit-tested (`tests/test_drivers.py`). This encoder is
   original LumiSync code, not a port of `python-idotmatrix`.
2. **BLE backend — done.** `IDotMatrixBleAdapter` uses `bleak` (bundled with
   LumiSync) on a private asyncio loop so the sync/Qt threads can call it
   synchronously. `discover()` scans for service `000000fa`. A missing `bleak`
   or address raises a clear error (never a silent no-op).
3. **Opcodes CONFIRMED via decompilation.** Brightness/power/color match the
   app's `BleProtocolN` byte-for-byte (golden-byte tests in `tests/test_drivers.py`).
   Writes use response=True and the driver subscribes to the notify characteristic.
   Derived independently from the app — not from any third-party library.
4. **Discovery UI — done.** The Add-Device dialog has a device-type selector
   (Govee LAN / iDotMatrix Bluetooth) with BLE-address + matrix-size fields, and
   the Devices tab has a "Scan Bluetooth" button (`DeviceController.scan_ble_devices`
   → `IDotMatrixBleAdapter.discover`). Device cards show a Bluetooth subline, and
   manual controls route through the adapter so a matrix device is controllable
   once its opcodes are confirmed.
5. **Sync targeting — done at the engine.** The sync engine now targets the
   `TransportAdapter` interface, so a matrix device receives
   `set_segments(screen_to_pixels(frame))` the same way a strip receives Razer
   frames. For matrices, prefer a 2D mapping and a lower frame rate for BLE.

## Caveats

- Brightness/power/color are confirmed from the app; the DIY per-pixel path is
  documented but not yet implemented (LumiSync uses ambient solid-color sync).
- Manual controls currently reconnect per action (multi-second BLE connect each
  time). A persistent per-device BLE connection in the controller would make
  control snappy — a worthwhile follow-up.
- A BLE panel typically allows only one central connection, so controlling a
  device manually while it is mid-sync can conflict.
- Some iDotMatrix variants expose only the `ae00` UART or Bluetooth Classic SPP;
  the driver targets `000000fa` (the model tested here).
