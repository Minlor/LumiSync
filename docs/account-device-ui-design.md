# LumiSync interface direction

Reading this as a desktop lighting control app for people managing their own
lights and smart plugs, in LumiSync's existing dark visual language.
ENERGY 1 / RHYTHM 2 / MOTION 2.

The account/device pass began at MOTION 1. The scope subsequently expanded to
the rest of the app; MOTION 2 describes the retained short navigation, drawer,
switch and expand/collapse transitions. They show a change of location or state,
never an endless decorative loop. Status lights are now steady.

The existing charcoal surfaces, blue action accent and Windows sans-serif type
come from `lumisync/gui/theme/tokens.py` and the current app. Dark surfaces suit
a screen-sync app used beside a display in a dim room. This work keeps that
identity and the existing Devices, Monitor Sync, Music Sync, Draw and Settings
destinations. The user chose to apply antislop-ui during implementation and
asked to retain useful controls within reach as windows shrink.

- Account layout: the sign-in form has the most space because connecting an
  account is the main task; the narrower account list supports refresh and
  disconnect. Narrow windows use Sign in / Connected accounts selectors;
  compact forms use inline labels and pin Sign in beside Close. Fields keep
  their values across a resize. At the minimum dialog height, scrolling remains
  available while the primary action stays visible.
- Provider choices: Govee, Tuya and LSC stay visible; alternative sign-in methods
  appear only for the selected provider, keeping API and QR details out of the
  password flow.
- Typography: the system UI sans-serif preserves legibility and Windows
  familiarity; larger values distinguish power draw and energy totals from
  field labels and supporting explanations.
- Spacing: labels stay close to their inputs, while sign-in, account management,
  electrical readings and usage history have separate groups.
- Surfaces: solid forms, device cards and controls preserve contrast. Native
  Acrylic or Mica is limited to the window foundation and navigation, with
  enough tint to keep text readable even over a bright desktop. Card shadows,
  power glows and repeating status pulses have been removed. No new logo,
  illustration or avatar is introduced.
- Accent: the sign-in action carries the account screen's blue emphasis; the
  daily usage series uses the same blue to identify measured energy.
- Device hierarchy: power and supported light controls come first; a metered
  plug prioritizes current watts, then voltage/current and monthly consumption.
  Technical device details are expandable so identifiers do not compete with
  everyday controls.
- Responsive devices: the inspector uses the main pane on narrower windows;
  it sits beside cards when there is room. Short, wide metered views place
  electrical readings beside monthly usage. Detailed daily rows remain
  expandable. Refreshing devices preserves the identity of an open inspector
  and the devices chosen for a group when the list order changes.
- Energy chart: daily bars answer which days consumed energy. Missing readings
  remain gaps, and a keyboard-accessible daily table preserves exact values.
- Monitor/Music: targets come first, then the mode's settings, with Start/Stop
  pinned below the scroll area. Roomy Music views show reaction buttons beside
  palette/output controls; short views use the same reactions in a dropdown.
  Explanations move to tooltips before essential controls lose space.
- Draw: the pixel canvas owns the workspace. Tools sit alongside it in a roomy
  window and above it in compact windows. Send and Stop stay visible; saved
  animation frames expand only when requested. Keyboard movement, painting
  and erasing make the canvas usable without a mouse.
- Settings: the existing section list becomes a section dropdown in a narrow
  window. Wrapping switch labels and form rows preserve full preference names.
  Advanced monitor tuning can scroll without hiding application preferences.
- Dialogs: Add Device keeps validation and submission below a scrolling form.
  Panel tools group the existing clock, display, countdown and scoreboard
  controls into mode tabs so inputs stay beside the command they affect.
- Feedback: errors describe the failed task and next action; empty accounts,
  devices and sync targets explain how to add content. Notifications have a
  keyboard-accessible dismiss button and render external messages as plain text.
- Identity motif: a quiet charcoal workspace, blue action/focus border, and
  nearby labels repeat across forms and device controls. Actual power/status
  colors and editable art carry the product's lighting character; no ornamental
  marketing layout is needed.
- Data: all product numbers come from devices or account responses. Visual
  verification uses clearly labelled, isolated fixtures without accessing
  personal credentials or sending hardware commands.

Verification, the recorded interaction coverage and the antislop delivery gate
are in [the UI verification report](account-device-ui-verification.md).

## Device layout and readings follow-up

Keep the existing ENERGY 1 / RHYTHM 2 / MOTION 2 direction. The device list is
a working inventory: equal column widths and aligned rows make power controls
easy to find. Fill the available width with readable cards instead of retaining
340 px boxes. A compact header combines a physical device glyph, its type and
its name; plug, wall switch, strip and matrix glyphs describe actual hardware,
with a generic light fallback when the model is unknown. Type also appears as
text, so recognition does not depend on color or an icon alone.

Keep the inventory beside the inspector whenever both panes can fit their
controls. Keep the page heading and toolbar above both panes: opening a device
must not move the description or actions. Align the inspector's top with the
first device row. Reserve two readable card columns beside a 400 px inspector
at the user's 1120 px window size, including vertical-scrollbar room. Base
toolbar wrapping only on the page width and measured button sizes. At narrow
widths or very short content areas, the inspector gets the content area below
the same header and toolbar. A wider compact inspector places readings beside
monthly controls to preserve access to power, Refresh and Load usage.
Its frame hugs the controls'
height, with scrolling for expanded details rather than a large empty box.
Remove the hidden group-selection row from normal layout spacing. Roomy rows
can use a little extra height to align with the available body, capped at 196 px
so a small inventory never becomes oversized tiles. Preserve normal font sizes and
44 px action targets; reduce wasted padding and duplicate descriptions first.

Request electrical readings immediately on opening and every five seconds
while visible. Coalesce queued reads by device identity, prioritize the open
device, and retain last reported values on failure. Show when status was last
checked; monthly usage remains an explicit, cached request.

## About and release documentation

Keep About as a compact support page: version and concrete device capabilities
first, update status beside its actions, then account setup and the repository.
Two short action rows replace the former stack of unrelated full-width links.
Credit Minlor and the MIT license in one quiet line. The existing typography,
solid surfaces and blue keyboard focus continue ENERGY 1 / RHYTHM 2 / MOTION 2.
README screenshots render the actual widgets with isolated, explicitly labelled
example devices, account labels and meter history; no personal settings,
credentials or physical commands are used. Documentation shows the inventory,
plug inspector, account sign-in and both sync workspaces because those are the
tasks a new user needs to understand.

The website keeps LumiSync's charcoal/blue identity and the same restrained
ENERGY 1 / RHYTHM 2 / MOTION 2 direction. Its first decision is downloading the
desktop app; the next is checking device/connection support and account setup.
Show current product captures with visible example captions. Separate local
sync from optional vendor cloud control, and put plug measurements beside their
meaning. Use a native comparison table, real documentation links and visible
keyboard focus. Remove duplicate eyebrow headings and decorative list dots;
screenshots and concrete capabilities provide the visual interest.
The three compact feature summaries correspond to the app's three entry tasks
(screen sync, music sync and device control), with equal weight because the
user chooses among them. Familiar monitor/music/control glyphs identify those
tasks; platform icons distinguish downloads, and arrows signal a download or
documentation destination. System Segoe UI follows the desktop application's
typography and falls back to the platform's UI font. Screenshot captions label
the examples without introducing promotional metrics or security claims.
