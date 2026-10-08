# Personal vendor accounts and Wi-Fi control

Open **Devices → Accounts** to connect an account. Network requests run in the
background. Imported lights, light switches and smart plugs appear alongside existing devices; existing local
connections and groups are retained.

Choose **Govee**, **Tuya** or **LSC**, then enter your email and password. The
**Sign-in method** selector offers API or QR alternatives only where supported.
Small windows separate **Sign in** and **Connected accounts**; shorter windows
use compact field rows and keep the primary action beside **Close**. Window
sizes are bounded by the available screen space, in Qt's DPI-aware coordinates.
Passwords can be temporarily revealed with **Show**. A verification request
opens a separate code step with **Send a new code** and **Back to sign-in**.

## Govee

Choose **Govee**, leave **Email and password** selected, and enter your own Govee Home
credentials. LumiSync exchanges the password for an account session, discovers
lighting devices with an IoT topic, and controls them through the account-issued
MQTT TLS connection. It subscribes to the account's topic for status replies.
The password is never saved to disk or the credential vault. If Govee requires
email verification, LumiSync requests the code and shows a code field in the
same dialog. Enter the code to finish; you do not need to retype the password.
Use **Send a new code** if needed; resends have a one-minute cooldown.

This uses the private app's v2 login and current device-list endpoint. Email
verification keeps the same client identity across requests. Incorrect
credentials, an unregistered account email, invalid verification codes, rate
limits and outdated protocols have distinct errors. Other security checks,
regional account differences, expiry or vendor changes can still prevent
login. There is no password-based
automatic re-login because LumiSync does not save the password.

Alternatively, select Govee's **API key** method and use your own key from Govee Home's
API-key application. This route uses the public Platform API and imports its
advertised capabilities. Power, brightness, RGB, and tunable white controls
are available when the device advertises them. Request pacing and rate-limit
errors apply. Appliances without lighting capabilities are filtered out.

For screen/music sync, enable LAN Control for a compatible device in Govee
Home, connect the PC to the same network, and choose **Find Devices**. The
inspector can switch between **Local Wi-Fi** and **Account Wi-Fi** when both
are available. Local Govee control needs no login.

## Tuya Smart and Smart Life

For Tuya Smart, choose **Tuya** and **Email and password**. Enter your
account email and password, then choose your account country from the dropdown
(for example, **Poland (+48)**). Use the same country selected in the phone app.
Account routing is automatic; there is no personal-account data center field.

LumiSync suggests the account country from the Windows home region, falling
back to the system locale when needed. This uses local OS information, with no
IP-location request. A country manually chosen during the current dialog takes
precedence over the last successful country for that provider, which takes
precedence over the OS suggestion.
The country stays editable because your PC's region can differ from your
account's country. Unknown regions leave the selector empty. Saved ISO country
codes distinguish countries sharing a calling code, such as Canada and the US.
The Windows lookup uses
[GetUserDefaultGeoName](https://learn.microsoft.com/en-us/windows/win32/api/winnls/nf-winnls-getuserdefaultgeoname).

The first connection reads login configuration from the matching Tuya Smart
APK/APKM/XAPK. LumiSync looks for the supplied APKM in Downloads automatically;
open **App setup options → Browse** if the package is elsewhere or automatic
setup fails. These optional settings are collapsed during ordinary sign-in and
open automatically when app configuration needs attention. This reads app data without running
Android code or requiring Android developer tools. The validated configuration
is saved in the system vault, or kept for this session when Remember is off.
Vendor application secrets are not bundled into LumiSync or saved in settings.

The client obtains a single-use RSA challenge and encrypts the password in the
format required by the vendor. If email verification is requested, the vendor
sends a code. Enter that code to finish; every attempt uses a fresh RSA
challenge. Password and code fields clear when submitted. While verification
is pending, the password stays only in memory for that sign-in attempt, for
up to 15 minutes plus an in-flight network request. It is cleared when the
attempt completes, is cancelled, changes account/provider/country or expires.
Only the resulting account session is retained. Expired sessions require login
again. Security challenges that need the vendor app are shown as errors.

Tuya Smart and Smart Life have separate app identities. **Smart Life accounts
use QR sign-in** with the currently supplied packages. Select **Tuya**, then
**QR sign-in**, and copy the user code from your
phone app's **Me → Settings → Account and Security**, enter it in LumiSync, and
request the QR code. Scan and approve it with the same phone app. Click
**I've approved the code. Finish sign-in**. Codes expire after three minutes;
request another if it expires.

LumiSync uses [Tuya's official device-sharing SDK](https://github.com/tuya/tuya-device-sharing-sdk)
and the public sharing registration used by Home Assistant. Consequently, the
phone's authorization screen names **Home Assistant**; the dialog discloses this.
The QR method requests no Tuya password or app package. LumiSync stores the
issued access/refresh session and saves refreshed tokens through the OS vault.
It does not connect an actual Home Assistant installation.

Lights, relay light switches, sockets and power strips are imported. Switch categories (`kg` and
related switch categories) require writable boolean relay functions such as
`switch` or `switch_1`. Socket categories (`cz`, `pc` and matching OEM variants)
are imported as power-only smart plugs. Curtains, unrelated appliances and
event-only buttons are excluded. Multi-gang switches and multi-outlet strips appear as separate
named controls, with independent power commands, status and saved identities.
Local gangs share one serialized TCP connection to their physical switch.
Controls and brightness ranges come from
the device's function specifications. RGB-mode brightness updates the HSV
value instead of the separate white-channel brightness DP.

Where the authorized response includes a usable local key, **Find Devices**
matches Tuya broadcasts by device ID and learns the LAN address and protocol
version. The broadcast itself contains no local key. Devices marked by Tuya
as unsuitable for local control stay on their cloud connection.

## LSC Smart Connect

LSC uses Tuya's OEM platform, but its accounts are separate from Tuya Smart /
Smart Life. Choose **LSC** and use
your own LSC account, select its country, and enter an email verification code
if requested. A Tuya project is not required for this method.

LumiSync reads the supplied LSC XAPK in Downloads automatically on the first
connection. Use **App setup options → Browse** for a package stored elsewhere. Configuration,
password clearing, verification, session storage, and regional routing follow
the same rules as Tuya Smart password login above. The supplied LSC 2.0.7 and
Tuya Smart 7.11.4 packages pass live signed-clock and RSA-challenge checks.
The linked Tuya account was also checked read-only: its `kg` / `switch_1`
light switch is discovered and returns online and power state. The user has
confirmed successful Govee, LSC and Tuya account login. Physical commands,
metering readings and monthly history still require validation on the user's plugs.

Both personal-account clients enumerate homes and device schemas, import
supported lights, light switches, smart plugs and authorized local keys, and send native device data points.
RGB values are translated between the mobile service's hexadecimal encoding
and LumiSync's controls. Gateway children use their parent gateway for cloud
commands and are not advertised as directly controllable LAN devices.

## Device controls and plug metering

Open a device card to see its supported controls. Power-only switches and
plugs have no brightness, colour or white-temperature controls. Dimmable and
tunable-white lights show their own supported controls; colour controls appear
only on colour-capable devices. Plugs and switches are excluded from sync groups
and cannot be selected as a default sync device. Known Govee catalogue flags
also determine colour and segment-sync availability. The authorized cloud-project
option has been removed from the sign-in form. Existing project connections
can still be refreshed or disconnected; the app does not erase saved sessions.

Choose **Find Devices** to refresh all linked accounts and their available
controls, as well as search the local network and Bluetooth. Each account
refreshes independently; a failed service does not discard successful results.
The same search updates saved device readings and matches newly imported Tuya
devices against local observations even when the local scan finishes first.
An account disconnected during a refresh is not imported again.
For a single account, select it under **Connected accounts** and choose
**Refresh devices**.

Supported plugs show **Electrical readings** in the inspector, with watts, volts,
amps and a device energy counter where reported. Readings use each device's
actual scale and unit. Opening the inspector requests a fresh reading, then
checks every five seconds while it is visible, or with **Refresh**. Reads are
coalesced while another query is running and stop when the inspector is hidden.
The time shown is the last status check; actual values change when the device
reports them. Unsupported readings are hidden; missing
readings show **Not reported**, and offline readings are labelled as last reported.

If the personal account exposes an incremental energy DP (`add_ele`), choose
a month and **Load usage**. LumiSync retrieves the vendor's daily energy
statistics and displays reported kWh, day coverage and a daily bar chart. Missing days remain
missing; the current day can be incomplete. A cumulative meter counter is not
summed as monthly consumption. Multi-outlet devices label shared readings as
covering the whole device. The latest increment reported by `add_ele` is not
displayed as lifetime usage. History is fetched on demand and cached in memory
for five minutes. Failure to retrieve history does not disable live metering
or power controls. It is not estimated from watts or from how long LumiSync
has been running.

**Show daily readings** reveals the exact values in a table; chart points also
support keyboard inspection. Device views put electrical readings and
monthly usage side by side when wide enough. Short views fold the daily chart into
that button. On narrower views they stack. Power, refresh and monthly loading
remain available without expanding technical details. Long tables, expanded
details and unusually small windows retain scrolling instead of shrinking text
or control targets.

The device inventory fills and balances its columns as the window changes.
Hardware icons and type labels distinguish LED strips, matrix panels, wall
switches and plugs using device metadata; unknown lights keep a generic label.
The inventory stays beside device controls when both panes fit. Simple device
panels fit their content height and expand for connection details and history.

Statistics depend on the product and the account service providing them. The
request follows [Tuya's statistics API](https://github.com/tuya/tuya-panel-sdk/blob/main/packages/tuya-panel-api/src/common/statistic.ts)
and [socket statistics example](https://github.com/Tuya-Community/tuya-ray-demo/blob/feature/socket-advanced/examples/public-socket-advanced/src/pages/stats/index.tsx).
The service can reject statistics for products without vendor-enabled history.

## Manual local setup

Manual Tuya setup supports protocol versions 3.1–3.5 and modern DP 20–24 or
legacy DP 1–5 layouts. Account imports can override DP IDs and numeric ranges
from the authorized specifications. Tunable-white endpoints default to
2700–6500 K; exact Kelvin limits are device-dependent. Nonstandard DP layouts,
gateways, Zigbee lights, and other device categories need their own mappings
and are not universally supported by the LAN driver.

## Credentials, refresh, and disconnect

**Remember this account** uses the Windows Credential Manager or a supported OS
secret service. Account tokens, API/project secrets, and Tuya local keys are
not written to `settings.json`. Settings contain account metadata and vault
references. Legacy plaintext local keys migrate to the vault on the next
settings write; existing copies or backups of old settings are not modified.
The account list masks email addresses, including labels saved by older
versions. New account labels contain only an email hint; the complete address
is retained only in the secure/session account credentials. The email input
also clears after a successful connection.

If no secure system backend is available, disable Remember for session-only
storage. Devices and account metadata remain saved, but reconnect after
restarting LumiSync. Manual Tuya additions also have a Remember-local-key
option. There is no plaintext-file fallback.

Choose an account and use **Refresh devices** to import current capabilities
or keys. **Disconnect account** removes its saved session and closes its
network connection. Separately imported local keys remain available for local
control. Removing a device stops displaying it; it does not unpair the device
from its vendor account.

## Sync and iDotMatrix

The Devices heading and toolbar stay above the inventory and inspector, so
opening a device leaves page actions in place. Six devices fit beside the
inspector at a 1120×792 window. Narrow or very short windows give controls the
content area; wider compact panels place readings beside monthly usage.

Rapid power/brightness changes keep the latest requested values while a write
finishes. Status replies begun before a newer command are discarded. Logs,
including native crash stacks, are available through **Settings → About → Open
logs folder**, or `~/.lumisync/logs`.

Cloud connections support manual controls and status. They do not accept the
high-frequency screen/music stream. Switch to a local connection first:
Govee LAN uses its zone stream, Tuya LAN uses one averaged ambient colour
with a five-update/second limit, and iDotMatrix BLE uses one ambient colour
with a ten-update/second limit.

The supplied iDotMatrix 2.1.6 app's password screen manages a six-digit Bluetooth
device PIN. No email-account login or Wi-Fi device-control path was established
for it. iDotMatrix remains a Bluetooth integration; device PIN management is
not implemented in LumiSync.

iDotMatrix 2.1.6 uses BLE for the investigated panel commands. Its internet
endpoints serve app content and related services; no generic Wi-Fi panel
control route was established. No account is needed for supported BLE panels.
Select a panel in Devices and open **Panel Tools** for time/clock styles,
180° rotation, countdown, and scoreboard. Draw provides static pixel art and
animations. Stop active sync/drawing before changing panel modes.

The account protocols and panel encoders are tested with fixtures. The linked
Tuya switch's discovery and status were verified read-only; physical command
acknowledgement, other account flows and firmware behavior still need
validation on the user's accounts and hardware.
