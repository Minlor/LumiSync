# Device integrations

Use [account setup](vendor-accounts.md) for sign-in, discovery, controls and
metering. This reference describes the implemented connections and their limits.

| Family | Connection | Available controls |
| --- | --- | --- |
| Govee | Local Wi-Fi, personal account, Platform API | Power, brightness, RGB and white temperature where advertised; local zones on supported models |
| Tuya / LSC | Local Wi-Fi, personal account | Lights and bulbs, relay switches, sockets and power strips; controls follow each device's function schema |
| iDotMatrix | Bluetooth LE | Matrix power, brightness, color, drawing, animation and supported panel tools |

Screen and music sync use supported local connections. Account connections
provide manual control. Device models and firmware determine the available
functions; importing an account does not establish every model's compatibility.
Switches and plugs expose power rather than unsupported light controls. Metered
plugs can report watts, volts, amps and daily/monthly energy in kWh. Missing
measurements remain unavailable, and old readings retain their last-reported label.

## Govee

Local discovery uses UDP multicast `239.255.255.250:4001`, receives replies on
port `4002`, and sends commands to port `4003`. Enable LAN Control in Govee Home
and allow local network traffic through the firewall. This connection needs no
account authentication.

The LAN adapter accepts `status` and `devStatus` responses, including `onOff`,
brightness and color. Power uses `turn` with `value` 0/1; brightness uses
`brightness` with `value` 0–100. RGB/white commands use `colorwc`, with a `color`
object and `colorTemInKelvin`. A successful UDP send is not a confirmed device
state, so background readbacks update the displayed status.

Personal email/password sign-in uses the vendor's HTTPS account service,
including email verification when required, followed by account-issued MQTT
over TLS. The Platform API key route uses the capabilities returned by the
public API. Both account routes are paced and handle expiry/rate limits.
The password is not retained for automatic re-login.

Zone support is model dependent. Devices with local and account connections
can select their transport in the inspector. Vendor desktop features that
depend on undocumented hardware-specific commands are not assumed to work.

## Tuya and LSC

Tuya Smart and LSC have separate account namespaces. Since 0.8.1, their
email/password methods include matching shared client configuration. Sign-in
uses signed HTTPS requests, a single-use RSA challenge, encrypted passwords and
vendor verification. The configuration cannot authorize an account by itself.
An Android package is only an optional advanced override if vendor changes
require one before a LumiSync update. Smart Life uses the separate QR method.

Account discovery reads device categories and function specifications rather
than assigning light controls to every product. Multi-gang switches and
multi-outlet strips produce independent named relay controls. Read-only buttons,
curtains and unrelated appliances are excluded. RGB-mode dimming changes the
HSV value; white-channel dimming uses the appropriate brightness function.

Local control uses TCP `6668` and Tuya protocol 3.3, 3.4 or 3.5 through
`tinytuya`. It needs the authorized device's local key. Broadcast discovery can
find an address and protocol version, but cannot supply that key. Where an
account response includes one, Find Devices matches it by device ID. Gangs on
one physical device share a serialized connection. Devices the vendor marks
unsuitable for LAN control stay on their account connection.

Open plug inspectors request electrical readings immediately and every five
seconds while visible. Requests are coalesced by identity; failures retain the
last known values. Monthly usage is an explicit cached request. Energy is
measured in kWh; voltage is an instantaneous value, not monthly consumption.

## iDotMatrix

The BLE adapter writes to `0000fa02-0000-1000-8000-00805f9b34fb` in the panel's
FA service and uses notifications where available. Packets carry a little-endian
length prefix. Writes use responses to avoid overrunning the panel.

Implemented commands cover power, brightness, solid RGB, DIY pixel data,
clock/display settings, rotation, countdown and scoreboard. Draw supports
static grids and frame animations. Sync uses ambient color; it does not imply
full video streaming on every panel. A panel PIN is not an account login. No
generic Wi-Fi account-control route is implemented for this family.

## Implementation and verification

- Account protocols and session storage: `lumisync/accounts/`.
- Device transports and capability mapping: `lumisync/drivers/`.
- Discovery and connection matching: `lumisync/devices.py` and `lumisync/connection.py`.
- Shared screen/audio processing: `lumisync/sync/processing.py` and `lumisync/sync/audio.py`.
- Device controls, metering and account forms: `lumisync/gui/`.

Regression fixtures cover protocol encoding, capabilities, authentication,
metering, command pacing, worker lifetimes and UI behavior. See the current
[verification record](verification.md) for executed tests and physical-validation
limits. Previous exploratory plans remain available in Git history.
