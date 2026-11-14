# Changelog

All notable changes to LumiSync are recorded here.

## 1.0.0 — 2026-11-10

### Windows installer

- The Windows release is now a per-user `LumiSync-Setup.exe` installer instead
  of a portable single-file executable.
- The installer offers **Start LumiSync when I sign in** and **Create a
  desktop shortcut**, both checked by default.
- Adds Start Menu and uninstall entries under
  `%LOCALAPPDATA%\Programs\LumiSync` without requiring an administrator prompt.
- The in-app updater downloads the installer, verifies its SHA-256 checksum,
  closes LumiSync cleanly, and restarts the app after installation.
- Installers and Linux AppImages now publish SHA-256 checksum files
  (`LumiSync-Windows-SHA256SUMS.txt`, `LumiSync-Linux-SHA256SUMS.txt`) alongside the
  release artifacts.
- Linux releases also ship `LumiSync-x86_64.AppImage.tar.gz`: extracting it
  gives an AppImage that is already executable, with no `chmod +x` step.
- Sign-in launches start quietly in the system tray instead of opening the
  window.
- Starting 1.0 while LumiSync 0.8 is still running in the tray now asks you
  to quit the old copy, so the two never fight over the same lights.
- Uninstalling closes a running LumiSync first.
- The installer offers **Allow LumiSync through Windows Firewall**: one
  administrator prompt adds an inbound rule for private and domain networks,
  so Govee LAN replies and LSC/Tuya announcements reach the app. In-app
  updates never ask, and uninstalling removes the rule.
- When Windows Firewall blocks LumiSync, the Devices page says so and offers
  **Allow**; Settings › General › **Network access** shows the firewall status.
  Public networks are never allowed; LumiSync explains how to set the network
  to Private instead.

### Updates

- Update checks at startup (and twice a day while running) no longer open a
  dialog: a notification and a dot on Settings point to **Update now** in
  Settings › About. **Check for updates** still asks directly.
- With **Download updates automatically** on, the update downloads quietly in
  the background and waits for **Restart & update**.
- Source and pip installs open the release page instead of running the
  Windows installer.
- Downloaded installers are cleaned up from the temp folder on the next start.

### Interface

- Simpler navigation rail with labels; **Draw** appears only when a matrix
  panel is set up.
- Device cards show one status line (for example "On · 80%", "Offline",
  "No status yet") instead of three "unknown" lines, and the dot is only green
  when the device is reachable.
- Find devices, Add device and Accounts sit on the Devices title row. Groups
  are created with **Save as group** on the sync pages and managed in
  Settings › Groups.
- Start/Stop now sits at the top of Monitor and Music Sync, the display picker
  moved onto the Monitor page, and lights that are only connected through an
  account get a one-line explanation of how to make them sync live (#53).
- LED layout gains **Shift start ◀/▶** to fix strips that start in a different
  corner (#18).

### Sync and lighting

- Monitor sync no longer hangs when the captured monitor is unplugged, sleeps
  or the GPU driver resets; it recovers and keeps Stop responsive.
- Govee strips get Razer mode re-sent during a sync, so a lost packet, device
  reboot or long pause no longer leaves them ignoring every frame (#49).
- Music sync listens to the default output's loopback by exact device, never
  silently to a microphone, says which device it is listening to, and hints
  when no sound arrives. Linux capture uses small buffers (no 2 s lag).
- The selected display now maps to the right monitor on multi-GPU systems.
- Brightness boost uses a soft knee and is applied before brightness, so the
  brightness cap is respected and bright areas keep their gradation.
- Lower CPU use: matrix panels stream one ambient zone, zone averaging is
  batched and shared between devices, and screen capture is released after
  a sync.
- Wayland sessions explain up front that Monitor Sync needs X11.
- Added a 100–150% Monitor Sync **Brightness boost** that lifts dim and
  midtone screen content while preserving hue.
- LED Mapping test colors now use the current Monitor brightness and boost, so
  the preview matches a live sync frame instead of appearing as a hidden
  overdrive.
- Added a hue-preserving tone-map helper shared by the GUI and CLI sync paths.

### LSC / Tuya reliability

- Tuya/LSC controls reconnect once after a stale idle TCP session instead of
  failing until the app is restarted.
- Local Tuya devices fall back to their linked vendor account when the local
  LAN path is unreachable.
- Key/protocol rejections refresh the device's local key from its linked
  account once before giving up.
- Cloud `DEVICE_OFFLINE` responses now produce a clear “device is offline”
  message instead of a raw vendor code.
- LSC `dd`-family wall washers and Battletron strips now normalize to the
  modern HSV/1000 local colour payload, fixing the rejected local colour
  commands reported in #52.
- Device status now refreshes in the background: local devices every 15 seconds
  and account-linked devices every 60 seconds while the Devices page is open.
- Local Tuya devices that were recently reached through the cloud use the cloud
  path for a short cooldown, avoiding repeated LAN timeouts; a fresh LAN
  presence beat immediately restores local-first control.
- Account-confirmed state is labelled **Online (account)** so it is clear when
  a device is being read through the vendor account.
- A rejected local key is refreshed from the linked account at most once per
  10 minutes, only on a real key error, saved securely, and no longer resets
  the connection; transient errors simply reconnect.
- An unplugged or offline LSC strip during sync backs off quickly instead of
  stalling every frame for seconds, and sync start skips its slow retries.
- Brightness after picking a colour now dims the colour (instead of writing
  the white-mode brightness), and the hue survives dimming to 0%.
- Settings saved by 0.8.2 get the modern LSC colour payload right away (#52).
- Only devices with a real paint data point are treated as RGBIC strips.

### Govee and network reliability

- The shared LAN listener survives network resets, and a dead socket is
  replaced automatically instead of silently dropping status replies.
- Status falls back to `devStatus` for models that only answer that query.
- H608x models stream with the standard colour frame.
- Malformed or hostile network packets can no longer stop status reads or the
  LSC presence listener.
- Bluetooth panels close within a second at exit.

### Stability and settings

- A settings file that is briefly unreadable (antivirus, backup, permissions)
  can no longer cause your devices, accounts or groups to be overwritten; saves
  pause until the file reads again, and they never drop unrelated settings.
- Saves keep working when the system keyring is unavailable, and local keys
  are written to the keyring once instead of on every save.
- Status refreshes for local and account devices run separately, account
  checks are rate-limited, and a slow vendor server can no longer freeze the
  local status dots.
- Save failures show a clear message instead of an "Unexpected Error" dialog;
  Windows file locks are retried.
- Unexpected errors show at most three dialogs per session, never from a
  background thread, and are logged without secrets.
- Quitting stays within its deadline even with Bluetooth panels or account
  sessions open, and signing out of Windows stops a running sync.
- Linux autostart points at the AppImage itself instead of a temporary mount.
- `--check-gui` and the test suite no longer touch your real preferences.

### Updates and release

- The update dialog now shows the release notes, offers **Skip this version**,
  and has an optional **Download updates automatically** setting.
- The installed `LumiSync.exe` now carries proper Windows file-version
  metadata.
- Release documentation and dependency auditing now match the installer-only
  distribution.

### Security

- Raised the dependency floors for `certifi`, `cryptography`, `pillow`, and
  `urllib3` after a clean `pip-audit` pass.
- Added a CI dependency-audit job and Dependabot updates for Python,
  GitHub Actions, and the website.

### Other

- Linux AppImage releases publish checksums and keep the existing X11 smoke
  test.
- Added regression coverage for Tuya reconnects, cloud fallback, local-key
  refresh, installer tasks, update downloads, background status polling, and
  the LSC wall-washer colour payload.
