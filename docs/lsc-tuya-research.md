# LSC Smart Connect (Tuya) Research + Integration Plan

Current implementation (8 October 2026): **Devices → Accounts** supports direct
LSC Smart Connect and Tuya Smart email/password login, including RSA challenges,
email verification, account-region routing, lighting-schema imports and cloud
commands. App-specific login configuration is read from the supplied Android
packages and stored in the OS vault. Live signed-clock and RSA initialization
checks pass for LSC 2.0.7 and Tuya Smart 7.11.4; real account/hardware completion
still needs validation. See [account setup](vendor-accounts.md).

> Historical investigation of version 2.0.4. Current 2.0.7 analysis, account
> integrations, secure storage and validation limits are in the
> [October 2026 audit](vendor-integration-audit-2026-10-08.md) and
> [account guide](vendor-accounts.md). Those supersede local-only assumptions
> and unfinished-work notes below.

Adding **LSC Smart Connect** devices — WiFi light bulbs, LED strips, plugs — as a
third device family in LumiSync, from the official app
`com.lscsmartconnection.smart` v2.0.4 (`LSC+Smart+Connect_2.0.4_APKPure.xapk`).

Method: read-only static inspection of the APK (unzipped DEX + native-lib string
tables). No dynamic instrumentation, no account used. This documents the wire
protocol and how LumiSync targets it; the app is the evidence, not a third-party
library.

## What the App Is

LSC Smart Connect is a **rebrand of the Tuya / "Smart Life" platform** (Action,
the European retailer, ships it for their LSC-branded smart-home range). The
decompile is unambiguous — the `config.arm64_v8a.apk` ships Tuya's native SDK:

- `libthingsmart.so`, `libThingSmartLink.so`, `libthing_security.so`,
  `libthingnetsec.so`, `libthing_security_algorithm.so` — Tuya's SDK was
  renamed to the "Thing" namespace (`com.thingclips.*`, 90k+ references in the
  DEX string tables).
- React-Native + V8 (`libreactnativejni.so`, `libv8android.so`) shell over that
  native SDK.

So this is **not** a bespoke protocol like Govee or iDotMatrix — it is standard
Tuya, which is well understood and versioned.

## The Tuya Local Protocol (confirmed markers)

String-table counts across the 11 `classes*.dex`:

| Marker | Meaning |
| --- | --- |
| `6668` | TCP control port (device command socket) |
| `6666`, `6667` | UDP discovery broadcast ports (v3.1 plain / v3.2+ encrypted) |
| `3.1` `3.2` `3.3` `3.4` `3.5` | protocol versions present |
| `localKey`, `local_key` | the per-device AES key |
| `devId`, `gwId`, `productKey`, `dps` | Tuya device-model fields |
| `GCM`, `ECB`, `hmac`, `AES` | crypto: ECB (≤3.3), GCM + HMAC session (3.4/3.5) |

How control works:

1. Devices are found on the LAN by their UDP broadcast on `6666`/`6667`
   (encrypted with a well-known global key for v3.2+).
2. Commands are JSON frames over **TCP `6668`**, AES-encrypted with the
   device's **16-byte local key**. Protocol 3.4/3.5 first negotiate a session
   key via an HMAC handshake, then use AES-GCM.
3. State is a set of **data points (DPs)** — numbered key/value pairs. For lights
   (Tuya category `dj`) the standard DPs, confirmed by the DP code strings in the
   app (`switch_led`, `bright_value`, `temp_value`, `colour_data`, `work_mode`,
   `scene_data`, `music_data`):

   Modern string-code schema ("v2", current LSC devices):

   | DP | code | value |
   | --- | --- | --- |
   | 20 | `switch_led` | bool on/off |
   | 21 | `work_mode` | `white` \| `colour` \| `scene` \| `music` |
   | 22 | `bright_value_v2` | 10–1000 |
   | 23 | `temp_value_v2` | 0–1000 (warm→cool) |
   | 24 | `colour_data_v2` | `HHHHSSSSVVVV` hex — H 0-360, S/V 0-1000 |

   Legacy schema ("v1", older bulbs): DP 1 switch, 2 mode, 3 bright (25-255),
   4 temp (0-255), 5 `colour_data` (`RRGGBBHHHHSSVV`, S/V as 0-255).

## The Local Key — the one hard requirement

Unlike Govee (open UDP JSON, no key) and iDotMatrix (open BLE, no key), a Tuya
device is **cryptographically locked**: without its local key you cannot decrypt
or send anything. The key is generated during pairing and held by the vendor
account service — **it cannot be recovered by decompiling the app**, and it is
different for every device.

The user must obtain it once, by one of the established routes:

- **Tuya IoT developer portal** — create a developer account, link the LSC/Smart
  Life app account, and read each device's `id` + `local_key` from the device
  list (this is exactly what `tinytuya wizard` automates).
- **Pairing capture** — sniff the local key during device onboarding.
- **App data extraction** — read it from the app's storage on a rooted device.

LumiSync now obtains authorized keys through account imports or manual entry.
Local keys are saved in the OS credential store, with references in settings.
Cloud controls are optional; the local driver still uses the per-device key.
LSC OEM linking depends on project/app-owner permissions and is not equivalent
to signing into Tuya Smart with the same email/password.

## Integration in LumiSync

1. **Driver — done.** `lumisync/drivers/tuya_lan.py` implements the shared
   `TransportAdapter` interface in two layers:
   - **Encoder (pure, unit-tested):** maps LumiSync's power/brightness/color/
     white operations onto the light DPs above — `encode_colour` (v1/v2 HSV
     hex), `encode_brightness`, `encode_temperature`, `build_color_command`.
     This LumiSync-specific mapping is pinned by golden-value tests in
     `tests/test_drivers.py` (no hardware or network needed).
   - **Transport (`TuyaLightAdapter`):** drives the encrypted session via
     **`tinytuya`** (bundled with LumiSync — no extra install step).
2. **Why `tinytuya` and not our own protocol.** The iDotMatrix driver
   deliberately ships LumiSync's own encoder because that protocol is a simple
   length-prefixed BLE format. Tuya is the opposite: five protocol versions,
   AES-ECB and AES-GCM, and an HMAC session handshake for 3.4/3.5 — ~2000 lines
   of security-sensitive code where a subtle bug means silent failure.
   `tinytuya` (Apache-2.0, the mature community standard behind
   Home Assistant's localtuya) handles exactly that, and is used only for the
   wire/crypto. The value LumiSync adds and owns is the DP mapping.
3. **Sync targeting.** Tuya lights have no fast per-segment stream, so the sync
   engine drives a bulb/strip as **one ambient light**: `set_segments` averages
   the frame to a single color and sets `colour_data`. Keep the sync frame rate
   low — Tuya devices choke above ~10 commands/second over the local socket.
4. **UI — done.** The Add-Device dialog gains an "LSC / Tuya (Wi‑Fi)" type with
   IP / Device ID / Local Key / protocol-version fields;
   `DeviceController.add_tuya_device_manually` persists it, and device cards show
   a `LSC/Tuya · protocol 3.x` subline.

## Caveats

- **Local key required** (see above). There is no LAN-only way around it.
- Color/brightness/power/white are implemented against the standard `dj` light
  DP model; unusual products (RGBICW addressable strips with per-segment DPs,
  plugs, sensors) expose different DPs and would need per-product mapping.
- `protocol_version` defaults to **3.3**. 3.4/3.5 devices must select the
  matching version or the session handshake fails.
- **Find Devices** matches LAN broadcasts to devices whose local keys were
  imported from an authorized account or entered manually. Broadcasts identify
  addresses and protocol versions; they do not disclose the local key.
