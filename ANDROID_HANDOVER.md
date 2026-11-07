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
- **What works: the web import in Chrome on the phone** (7 Nov 2026, CONFIRMED: the import page showed "Import abgeschlossen" for a 339-byte
  export of a short test ride). Garmin Connect in Chrome, cloud-upload icon in the top
  bar, page `https://connect.garmin.com/app/import-data`; the `.fit` is chosen from Downloads (the app already copies every export there).
- **Built:** the Setup button "Open Garmin import page (Chrome)" (`openGarminImportPage` in `MainActivity.kt`: `ACTION_VIEW` of that URL with package
  `com.android.chrome` so Garmin's app, which claims several `connect.garmin.com/app/...` paths, does not take the link; falls back to the default browser).
  A plain link: no password, no internet permission in the app. After an export the status line tells the user to tap it and pick the file. Installed
  and shown on the phone; the tap itself has not been tested by me.
- Not tried: a Garmin device sync trick (copy the `.fit` into the watch's activity folder over USB so the app's normal sync uploads it; unverified).

## 5b. Training load, recovery and importing next to the watch (thought of 7 Nov 2026, not yet tested)
- **Finding (user forum reports, nothing official):** Garmin computes Training Effect, Training Load and Recovery Time ON THE DEVICE while it records and
  stores them in the file; Garmin Connect mostly reads them back. Third-party files (MyWhoosh, Zwift, FulGaz imports) reportedly get no load, so they do not feed
  Training Status or Recovery Time. Our `.fit` has manufacturer DEVELOPMENT and no load fields, so no load is expected. Unverified for our file.
  A reported workaround is editing the file to claim a Garmin manufacturer; deliberately NOT built (it would misstate where the data came from).
- **The user's idea:** let the Forerunner record the ride as well (it provides the load and recovery from its own heart rate) and import our file too.
  Load/recovery would then not be doubled IF our import adds none. But activity count and total time ARE doubled (two rides for the same hour); our file has no
  calories so those are not doubled; intensity minutes unknown; Garmin may reject an import it takes for a duplicate (close start times); Garmin Connect has no
  merge function. Our file would carry the power, cadence and dynamics (and speed/distance only if a speed sensor is connected).
- **Options for the user:** (1) import both and accept doubled time; (2) keep the watch ride in Garmin for load and totals and keep our file as the detailed
  dataset for the custom analysis, importing it only when the power curves should be in Garmin (recommended); (3) import ours and delete the watch ride (loses load).
- **Test to run at the pedal test:** note acute load, activity count and weekly time before importing our file; after the import check that load did not change,
  that the import was not rejected as duplicate, and which totals moved. Then record the result here and let the user pick an option.

## 5c. Pedal findings (technical; personal results are NOT kept in the repo)
- **The Favero Assioma PRO MX-2 already sent the cycling dynamics pages** (0x10, 0x13, 0xE0, 0xE1, 0xE2, battery 0x52, product 0x50/0x51) without any write
  from the app, so the read-only rule held. Whether other pedals need a display to enable them is unknown.
- **Per-side data is not per crank cycle.** Pages 0xE0 (right) and 0xE1 (left) arrive about every 2.25 s per side; the counter step per update (median 2) is not the
  number of crank turns, so per-cycle analysis from this data is invalid. Per-side values are averages over the pedal's update interval.
- **Angle statistics.** Power-phase start angles straddle 0/360 deg: use an unwrapped median or a circular mean, never a plain mean (Garmin's plain-mean start angles are
  skewed by the wrap). End angles and the peak-torque window do not wrap and agree with Garmin to the degree, which validates the decoding and the `.fit` export.
- PCO sign: positive = force applied outward (away from the bike), as defined in the profile.
- **Analysis scripts:** `android/analysis/make_report.py` (full report) and `make_sets.py` (work repetitions vs one easy section, crank diagrams), usage in their headers:
  `python -I android/analysis/make_report.py <data folder>`. The data folder holds `capture_*.csv` and `samples_*.csv` pulled from the phone; the default is the git-ignored
  `.claude/tmp/pedal_test`. Recordings and any personal analysis (results, body or fit notes) live only in git-ignored places (`.claude/tmp`, `.claude/notes`) and must never be committed.

## 6. What was learned (details in the skill)
- Stick reset after ~37 s came from a thread that wrote setup commands while nobody read (serial queue overflow, event 0x34); fixed by a pure reader thread.
- Screen lock does NOT stop recording once a foreground service holds it; my first diagnosis ("screen lock kills it") was wrong, the logs showed the real cause.
- Cycling dynamics are off until a display enables them (profile 4.5.2); the Favero datasheet says the pedals use the standard ANT+ PWR profile.
- `.fit` names come from the ride start so several rides in one connection never overwrite each other.
- Process: I once made an unrequested write default ON; the user caught it, then set the read-only rule. Ask before anything that touches hardware.

## 7. Working with the user
Wants: short factual answers, the real state said plainly (including my own mistakes), big readable UI, no scrolling, black screen, controls up top.
Does not want: writes to the sensors, being asked to type values while riding, secrets in the chat. Commits on request, straight to `main`.
