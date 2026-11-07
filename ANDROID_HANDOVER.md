# Android ANT+ logger: handover

Read this first for any work in `android/`. Last updated 7 Nov 2026 (end of the first long session).
How to build, install and debug: see the skill `.claude/skills/android-ant-logger/SKILL.md`
(a tracked file inside the otherwise git-ignored `.claude/`).

## 1. Goal
A standalone Android app that records every ANT+ signal through an ANT USB stick on an OTG adapter, on a non-rooted
phone, and exports a `.fit` that Garmin Connect accepts. Motivation: the user's new Favero Assioma PRO MX-2 pedals send
cycling dynamics (balance, power phase, PCO, effectiveness, smoothness, rider position) that MyWhoosh and the Garmin do
not record. The Favero Assioma app is the reference for what to show; the FIT only gets what Garmin supports.
Later (not started): decode and show the same on the Windows ride app (`ant/profiles.py` ignores balance, 0x13 and dynamics).

## 2. Hard constraints
- **Read-only toward sensors.** Never write to a sensor. Calibration and crank length are done with the official Favero
  app. Enforced in code (`Ant.STICK_CONFIG_MSGS`, `Ant.requireReadOnly` in `UsbAnt.write`, tests in `AntProtocolTest`).
  The removed enabling code is in git history (b7fda8d). The user would accept ONE narrow exception (enable dynamics only)
  if the pedal test shows it is needed: fixed message, read-first, explicit confirm button, never automatic, tests; ask first.
- The repo is **public**: no keys, no recordings, no secrets. Signing key and its properties file live in
  `E:\jschlager\keys\` and are the user's; never create/read/write them (the harness shows file changes to me).
- Safety/etiquette rules are also in `CLAUDE.md`.

## 3. Status
**Verified on real hardware (heart-rate watch only):** stick opens over OTG without root; setup ~3 s; recording survives a
locked screen (foreground service + wake lock); sensor dropout and return (auto re-acquire); HR gate and ride markers; `.fit`
import into Garmin Connect (dropouts show as gaps); automatic export on stop; recovery export of an unfinished ride at app start
(tested with a cut-off capture); idle-dim; two-page UI with live data on screen.
**Not verified (no pedals / sensors available yet):** everything from the pedals (page 0x10 balance, 0x13, dynamics pages 0xE0/E1/E2/0x14,
battery), speed/cadence sensors (decoding tested with constructed values only), the signed release APK on a second phone.
Tests: 32 JVM unit tests pass (`AntProtocolTest` 5, `DecoderFitTest` 27).

## 4. How it works
- `UsbAnt.kt` bulk transfers; `AntProtocol.kt` framing, message IDs, the read-only guard; `AntSession.kt` two threads (reader: read/decode/log;
  setup: stick configuration, watchdog), 6 receive channels (HR 120, power 11 x2, CSC 121, cadence 122, speed 123), ride start/stop, HR gate;
  `Decoder.kt` page decoding (`Decoder`), stateful speed/cadence/distance/RR (`SensorTracker`), capture reading (`CaptureReader`, latest ride wins,
  unfinished ride = START without STOP); `FitExporter.kt` 1 Hz records, holds a value 3 s then leaves a gap, native FIT dynamics fields;
  `RecorderService.kt` foreground service; `MainActivity.kt` two pages, recovery, auto export; `DashView.kt`/`LiveView.kt` scaled drawing.
- Decoded pages: 0x10 power/cadence/balance, 0x13 torque effectiveness and pedal smoothness, 0xE0/0xE1 power phase, 0xE2 PCO + rider position,
  0x14 barycenter, 0x50/0x51/0x52 product and battery, 0x02/0xFE the dynamics settings the sensor reports (shown under "Pedals report", read from
  its broadcast, never requested).
- Files per connection: `capture_<time>.csv` (raw, also frames we sent marked `,T`, markers RIDE_START/RIDE_STOP/WHEEL_MM), per ride `samples_<time>.csv`
  and `ride_<start>.fit` (+ copy in Downloads).

## 5. Open items and next steps (in order)
1. **Pedal test.** Connect with the pedals spinning; read Setup ("pages heard", "Pedals report") and the Live page; pull the capture and list which pages
   arrive and whether the pedals appear as one or two devices. Outcomes: dynamics already on (done) / sensor reports them off (decide on the narrow write
   exception with the user) / nothing reported (look at the raw page list).
2. Compare with the Favero app screenshots (the user can drop them in `.claude/tmp`): live display features there that we lack. Settings in that app
   (calibration, crank length, standby, travel mode, firmware) are out of scope by the read-only rule.
3. Speed/cadence sensor test with a real sensor; wheel circumference is typed (the display owns it; no calibration helper wanted, indoor use).
4. Small items the user may want: dim delay configurable (15 s fixed now), landscape layout (portrait locked), app icon, a better `versionName`
   (still `0.1-spike`), export of the previous unfinished ride is done, Python-side decoding for the Windows app.
5. **Debug and release share one key and one applicationId** (debug is signed with the release key when `ANTLOGGER_SIGNING_PROPS` is
   set), so they swap with `adb install -r`. A build made WITHOUT that variable is signed with the generic debug key and will not install
   over one signed with the user's key (uninstalling deletes the app's captures: pull them first). Same `versionCode` reinstalls fine; a lower one
   needs `adb install -r -d`. Never give out a debug APK.
6. **Sharing the app:** signed release APK works (build described in the skill and README). Google developer verification starts affecting
   sideloaded apps globally in 2027: register the package under a free limited-distribution account (up to 20 devices) or pay $25 for full.
   Play Store is not worth it for one friend (new personal accounts need a closed test with 12 testers for 14 days before production).

## 5a. Getting the .fit into Garmin Connect (investigated 7 Nov 2026; do not repeat)
The user finds the manual web import cumbersome. Findings:
- **The Garmin Connect Android app (5.31) cannot import activities.** It declares VIEW filters for `.fit`/`.gpx`, but opening a ride's `.fit` there starts
  "create a new COURSE" (asks road/gravel/MTB; no indoor): a route import, not an activity upload. Its only share target is plain text (waypoints).
  A button that opened the last ride in Garmin Connect was built, tested by the user, and removed again. Samsung's file manager also reports "no app"
  for `.fit` (it opens with a generic MIME type that Garmin's handler does not match).
- **No official upload API for individuals:** the Garmin Connect Developer Program (Activity API) is for approved business partners and delivers data FROM
  Garmin; new access was reported paused in 2026. A personal API token (2026) is pull-only.
- **Reusing the Garmin app's stored login is impossible** on a non-rooted phone (private app storage) and is not acceptable anyway.
- **Unofficial upload with the user's Garmin password** (community libraries, undocumented endpoint): unverified, unsupported, risks the account and would
  need internet permission and stored credentials in the app. Advised against; the user cares about security.
- Remaining options: web import (desktop, or the phone's browser in desktop mode: the Android file picker may or may not list `.fit`; untested), or
  a Garmin device sync trick (copy the `.fit` into the watch's activity folder over USB so the app's normal sync uploads it; unverified for the user's watch).
- If the phone browser import works, a button that opens `connect.garmin.com` import page would remove the navigation (note: Garmin's app may claim
  `https://connect.garmin.com/...` links; check that the page opens in the browser).

## 6. What was learned (details in the skill)
- Stick reset after ~37 s came from a thread that wrote setup commands while nobody read (serial queue overflow, event 0x34); fixed by a pure reader thread.
- Screen lock does NOT stop recording once a foreground service holds it; my first diagnosis ("screen lock kills it") was wrong, the logs showed the real cause.
- Cycling dynamics are off until a display enables them (profile 4.5.2); the Favero datasheet says the pedals use the standard ANT+ PWR profile.
- `.fit` names come from the ride start so several rides in one connection never overwrite each other.
- Process: I once made an unrequested write default ON; the user caught it, then set the read-only rule. Ask before anything that touches hardware.

## 7. Working with the user
Wants: short factual answers, the real state said plainly (including my own mistakes), big readable UI, no scrolling, black screen, controls up top.
Does not want: writes to the sensors, being asked to type values while riding, secrets in the chat. Commits on request, straight to `main`.
