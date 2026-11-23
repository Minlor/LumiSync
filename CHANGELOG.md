# Changelog

All notable changes to LumiSync are recorded here.

## Unreleased

- Settings is split into four tabs, General, Sync, Updates and About, picked
  from a row under the title (arrow keys or Ctrl+Tab also switch). The last
  tab opened comes back on the next start, and an update notification or the
  dot on the Settings icon opens the Updates tab. Tabs with a single group no
  longer repeat the tab name as a heading.
- Monitor sync works on Wayland (experimental): LumiSync shares your screen
  through the desktop's screen-sharing portal and PipeWire. Your desktop asks
  once which screen to share, and remembers it where the desktop supports
  that. Needs xdg-desktop-portal (GNOME, KDE or wlr) and GStreamer's PipeWire
  plugin; without them Monitor sync says what to install.
- Music sync on Linux: "Current Artwork" colours come from the playing MPRIS
  player's cover (needs `busctl` or `gdbus`).
- Music sync on Linux: turning the system volume down no longer dims the
  lights on PulseAudio/PipeWire outputs with software volume (read with
  `pactl`); hardware-volume outputs are left alone.
- Linux: LumiSync updates itself in place when run from an AppImage you can
  write to; the download is checked against a signed
  `LumiSync-Linux-SHA256SUMS.txt` before it replaces the file. Other Linux
  installs still open the release page.
- Linux: when ufw or firewalld is active and a search finds no local devices,
  the Devices page explains why and can copy the commands for UDP 4002 and
  6667. LumiSync never changes the firewall itself.
- The sidebar has GitHub and Ko-fi links above Settings, and About links to
  the repository, the author's GitHub and Ko-fi.
- Gigabyte RGB Fusion 2 motherboard lighting over USB (Windows and Linux):
  Find Devices now finds the board's lighting controller (verified on the B550
  AORUS ELITE, ITE IT5702, under Windows and under a Linux kernel in WSL2), with
  power, brightness and one colour for the whole board, and it can be a Monitor
  or Music sync target. It has not been run on a native Linux desktop yet. On
  Linux the controller
  is driven through hidraw and needs the udev rule in
  `packaging/linux/60-lumisync-pc-lighting.rules`; until it is installed the
  board is still listed and the first command explains how to add it.
- Logitech G502 HERO mouse lighting over USB, driven directly through the
  mouse's HID++ 2.0 protocol with no Logitech software API: it shows up on the
  PC page with power, brightness and one colour for the whole mouse (logo and
  DPI lights), and it can be a Monitor or Music sync target, in the
  background. Verified on a wired G502 HERO (`046D:C08B`) under Windows with
  Logitech G HUB running; G HUB may put its own colour back when its profile
  changes. Not yet tried with G HUB closed, and not run on Linux (the udev rule
  file now covers the mouse too, so it can be tried there). Colours are only
  shown, never saved to the mouse. Like the board, the mouse has colour
  correction (sRGB to the LEDs' linear drive, with green at 88% so white is
  no longer green-tinted; on by default) and a per-zone layout in the device
  panel: during sync the DPI lights and the logo each get their own colour.
  Both options apply at once, also during a running sync. The "Mouse
  lighting" card also picks the mouse's own effects, fixed colour, breathing
  or colour cycle, with a speed for the two animated ones (both checked on the
  mouse), and an optional separate logo colour while the DPI lights keep the
  main colour. Sync still sends fixed colours; when it stops, the chosen
  effect and colours come back if they were set since LumiSync started.
- The Lorgar keyboard no longer turns white when sync stops before any manual
  colour, brightness or power change was made: it keeps the last sync colour.
- Lorgar AZAR keyboard lighting over USB (`320F:5075`): one static colour for
  the whole keyboard, plus brightness (the keyboard's five levels) and power,
  set from the PC page. The "Keyboard lighting" card in the device panel also
  picks one of the keyboard's 17 built-in effects (Wave, Breathing, Stars,
  Rain and the rest, in the order of the vendor app), with speed, direction
  and multicolour where the effect has them; effects run on the keyboard
  itself. The keyboard saves every change to its internal memory, which wears
  out with use, so manual changes are sent at most four times a second and
  Monitor and Music sync is off by default: turning on "Monitor and Music
  sync" on its card (after a warning) makes it a sync target. During sync the
  whole keyboard follows one colour, updated at most every 10 seconds by
  default ("Sync updates" also offers every 2 seconds or up to 4 a second) and
  only when the colour changes noticeably; when sync stops the keyboard's own
  lighting comes back. Static and Wave were checked on the keyboard; the other
  effects' options follow the vendor app. Not yet run on Linux (the udev rule
  file covers it).
- The PC page finds supported hardware by itself: it scans this PC shortly
  after start, when the page opens and every few seconds while it is shown, so
  newly plugged lighting appears and unplugged lighting shows as not connected
  without selecting Scan this PC.
- Brightness per light for Monitor Sync and Music Sync: under each page's
  Brightness slider, "Brightness per light" lists the selected lights (a
  group's lights individually), each with its own 10-100% level on top of the
  page's brightness. A light's level is shared by both modes, saved, and
  applies to a running sync straight away; "Reset" puts the listed lights back
  to 100%.
- RGB Fusion 2 colours are converted from sRGB to the LEDs' linear drive, so
  dim and pastel colours no longer look washed out (on by default; "Colour
  correction" in the device panel turns it off). Colour correction also applies
  a white balance (green 84%, blue 59%), tuned by eye on the B550 AORUS ELITE,
  so white looks neutral instead of blue-green. The device panel also offers
  a per-zone layout: each of the board's zones (seven on the B550 AORUS ELITE,
  eight numbered zones on other boards) gets its own colour during sync.
- New **PC** page in the navigation rail for lighting inside or attached to
  this computer: RGB Fusion 2 motherboard lighting (which moves there from the
  Devices page) and Logitech mouse lighting. **Scan this PC** looks for it without a network
  search; Find Devices still finds it too, and a dot on the PC icon points to
  anything new. Shown on Windows and Linux.
- New motherboard, mouse and PC icons, and a clearer LED strip icon.
- Fixed Mica and Acrylic windows showing the previous page, and old sidebar
  markers, through the current one after switching pages, and slowly turning
  opaque.
- The Settings › Status bar option and the status bar itself have been
  removed; progress messages still go to the log, and anything you need to
  see appears as a notification.
- Acrylic is more see-through (the background tint is 60% instead of 84%),
  so the blur shows and the content sits closer to the title bar.
- Sidebar icons are larger, and the Music and light bulb icons are redrawn
  as outlines to match the rest.

### Updates

- In-app updates are now installed only when the release is signed with
  LumiSync's own key: the release's checksum file carries a signature that the
  app checks against the key built into it before it downloads the installer.
  A release without a valid signature is not installed, and the app says so;
  it can still be downloaded from the releases page. This is LumiSync's own
  signature, not a Windows code-signing certificate, so Windows SmartScreen
  may still warn the first time you run a manually downloaded installer.

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
