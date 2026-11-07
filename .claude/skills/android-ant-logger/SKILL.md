---
name: android-ant-logger
description: Build, test, install, debug, release or extend the Android ANT+ logger in android/ (Kotlin, ANT USB stick over OTG, FIT export). Use when asked to change the Android app, analyse a capture from the phone, install on the phone, make a signed APK, or decode more ANT+ pages. Holds the hard rules, the Windows/adb workflow and the pitfalls found the hard way.
---

# Android ANT+ logger: how to work on it

Read `ANDROID_HANDOVER.md` (repo root) first for status and open items, then this file for the how.

## Hard rules (the user set these; do not bend them)
1. **Read-only toward sensors.** The app only listens. Never add anything that transmits to a sensor
   (calibration, crank length, capabilities/8 Hz "set", acknowledged/burst/broadcast data). The guard is
   `Ant.STICK_CONFIG_MSGS` + `Ant.requireReadOnly` in `AntProtocol.kt`, called from `UsbAnt.write`, covered by
   `AntProtocolTest`. Do not weaken it. An earlier "enable cycling dynamics" write existed (commit b7fda8d) and
   was removed on purpose; the user offered ONE possible exception for enabling dynamics only, and only if the
   pedal test shows it is needed. Ask before building it.
2. **Never create, write or read a file the user will put a secret into.** The harness shows me file changes, which
   leaked a keystore password into the transcript once. Signing details live in
   `E:\jschlager\keys\signing.properties`, created by the user; I only pass its path in an environment variable.
3. **Scratch files go in `E:\jschlager\Documents\Projects\AntDongle\.claude\tmp`** (git-ignored), never `%TEMP%`.
4. Git: commit and push to `main` only when asked, stage by name, run the unit tests first, never include the
   user's own uncommitted files (`.gitignore`, `static/*`). The repo is PUBLIC: no keys, no recordings.
5. **Phone etiquette.** The phone is the user's personal phone. Screenshots can capture private content, so only
   take them when the user asked or while they are testing the app, delete them afterwards, and never tap or
   launch things while they are using it. Installing kills an active recording: check first (below).

## Environment (Windows, PowerShell; last known good)
- JDK 17: `C:\Program Files\Eclipse Adoptium\jdk-17.0.21.9-hotspot` (winget `EclipseAdoptium.Temurin.17.JDK`).
- Android SDK: `%LOCALAPPDATA%\Android\Sdk` (platform 36, build-tools 36.0.0). `android\local.properties` holds `sdk.dir`
  (git-ignored). `adb` from winget `Google.PlatformTools`; in a fresh shell refresh PATH:
  `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`
- Gradle wrapper 8.11.1, AGP 8.9.1, Kotlin 2.1.0, Garmin FIT SDK `com.garmin:fit:21.176.0`, minSdk 26, target/compile 36.
- Phone: Samsung Galaxy A33 5G (SM-A336B), Android 16. Wireless adb without pairing: with the cable on run
  `adb tcpip 5555`, then `adb connect <phone-ip>:5555` (last IP 192.168.178.33), then unplug (the OTG port is needed
  for the stick). Lasts until reboot. Developer mode is only for me; the app does not need it.

## Build, test, install
```powershell
$env:JAVA_HOME = (Get-ChildItem "C:\Program Files\Eclipse Adoptium" | Select-Object -First 1).FullName
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
# debug AND release are signed with the user's key when this path is set, so they can replace each other with
# `adb install -r` (same applicationId). Without it debug falls back to Android's generic debug key and will NOT
# install over an app signed with the user's key (signature mismatch). The file holds a secret: pass the path only.
$env:ANTLOGGER_SIGNING_PROPS = "E:\jschlager\keys\signing.properties"
cd E:\jschlager\Documents\Projects\AntDongle\android
.\gradlew.bat testDebugUnitTest assembleDebug
# results: app\build\test-results\testDebugUnitTest\*.xml  (32 tests at last update: 5 AntProtocolTest, 27 DecoderFitTest)
# is a session running?  0 = idle, safe to install
adb -s <ip>:5555 shell "dumpsys activity services app.antlogger | grep -c ServiceRecord"
adb -s <ip>:5555 install -r app\build\outputs\apk\debug\app-debug.apk
adb -s <ip>:5555 shell am start -W -n app.antlogger/.MainActivity
```
Run the unit tests before every commit. Hand-compute test bytes from the spec tables; do not derive them from the code.

## Looking at the phone and its data
- Screenshot (binary redirect needs cmd): `cmd /c "adb -s <ip>:5555 exec-out screencap -p > E:\...\.claude\tmp\x.png"`, then Read it.
  Screenshot pixels are 1080x2400; the Read tool shows them scaled by 1.2 (multiply coordinates by 1.2 for `adb shell input tap`).
- App files: `/sdcard/Android/data/app.antlogger/files/` has `capture_<time>.csv` (raw log), `samples_<time>.csv` (decoded, per ride)
  and `ride_<ridestart>.fit`; Downloads gets a copy of each `.fit`. `adb pull` works. `run-as app.antlogger cat shared_prefs/settings.xml` works (debug build).
- Capture format: `ts_ms,<raw ANT frame hex>`; `ts_ms,<hex>,T` = a frame WE sent to the stick; markers `ts,RIDE_START`, `ts,RIDE_STOP`, `ts,WHEEL_MM,<mm>`.
  Analyse with `python -I` (frame = A4 LEN ID DATA CHK; msg 0x4E broadcast with extended data: byte 9 flag 0x80, devnum bytes 10-11, devtype 12).
- Reports from a pulled ride: `python -I android/analysis/make_report.py <data folder>` and `make_sets.py <data folder> --easy=MM:SS-MM:SS` (work repetitions vs one easy
  section, crank diagrams). Data folder default `.claude/tmp/pedal_test`. Personal results stay in git-ignored `.claude/notes`; never commit recordings or personal analysis.
- Device test for a code path: plant a modified capture into the files dir, restart the app, check the result, then DELETE the test files
  (also from Downloads). Used to verify recovery of unfinished rides.

## ANT lessons learned on this hardware
- Stick: ANT USB Stick 2 (0FCF:1008). Reader and writer MUST be separate threads: a reader that also writes lets the stick's serial queue
  overflow (channel event 0x34), the stick resets itself (startup message flags 0x20) and setup stalls ~37 s. Always reset the stick at
  session start (a killed app leaves channels open) and reset it on a clean stop.
- Wildcard receive channels with HP search timeout 0 and LP search timeout 255 (infinite): a sensor that disappears (RX_FAIL 0x02, then
  GO_TO_SEARCH 0x08) is picked up again automatically. ENABLE_EXT (0x66) gives the device number in each broadcast.
- Watchdog: a STARTUP (0x6F) message after setup means an unexpected reset: re-configure the channels.
- Heart rate pages carry beat time (bytes 4-5, 1/1024 s), beat count (6) and bpm (7); RR = time difference of consecutive beats.
- Event-counted pages (0x13, 0xE0, 0xE1, 0xE2, 0x14) repeat identical payloads: de-duplicate those, not 0x10/HR.
- Speed/cadence sensors send revolution counters and event times only; the display owns the wheel circumference (ANT+ profile 9.6).

## The spec
ANT+ Bicycle Power profile Rev 5.1 (D00001086) is public at
`https://forums.garmin.com/cfs-file/__key/communityserver-discussions-components-files/402/D00001086_5F00_ANT_2B005F00_Device_5F00_Profile_5F002D005F00_Bicycle_5F00_Power_5F00_Rev_5F00_5.1.pdf`.
WebFetch cannot read it (binary): download with `curl.exe -L`, then `pdftotext -layout` (poppler ships in Git for Windows' mingw64) and read the text.
Useful sections: 4.5 cycling dynamics and how a display enables them, 8 page 0x10, 12 page 0x13, 15.2.5 Advanced Capabilities 2 (page 0x02/0xFE),
17 pages 0xE0/0xE1/0xE2/0x14, 19.2.1 request data page. Favero datasheet: Assioma PRO MX-2 uses the standard ANT+ PWR profile and sends PCO, power
phase, torque efficiency, pedal smoothness over ANT+ (rider position ANT+ only).

## UI conventions the user asked for (keep them)
Two pages (Setup, Live), swipe or tabs, no scrolling, everything scaled from the display (no fixed px/dp; `DashView.unit()` sizing), smallest text ~15 dp,
true black theme, status banner on top of both pages (connection, REC time, HR, power, speed), controls right under the banner, one dual-use
Start/STOP recording button on Setup (heart rate required to start, cadence/speed optional), no Stop button on Live, `.fit` exported automatically
when a recording stops and named after the ride start, a Setup button opens Garmin's import page in Chrome, unfinished rides exported at the next app start, "Dim screen when idle" checkbox (15 s, a touch
restores and is swallowed), portrait only, Disconnect disabled while recording.

## Pitfalls (each cost time)
- Kotlin: do not name view properties `left`/`right` (clash with `View.getLeft()`); in Gradle KTS `java.io.File` does not resolve (`java` is an extension): import it.
- PowerShell: `Invoke-WebRequest` hangs on big downloads (use `curl.exe`); `Select-String` pipelines after native tools show exit code 255 even when the build succeeded
  (read the BUILD line); `sdkmanager --licenses` needs an input file with many `y` lines; the `!` shell prefix is Git Bash (use forward slashes there).
- A debug build and a release build have different signatures: `adb install -r` of a release over a debug fails, and uninstalling deletes the app's captures.
- Getting the `.fit` into Garmin Connect from the phone: do NOT build "open with Garmin Connect". The Android app opens a `.fit` as a course import (asks for a
  track type), not as an activity. No official personal upload API exists; reusing the Garmin app's login is impossible; the password-based unofficial route is
  advised against. What works: Chrome on the phone, `https://connect.garmin.com/app/import-data` (cloud icon in the top bar), pick the `.fit` from Downloads.
  See `ANDROID_HANDOVER.md` section 5a. To read a tab's URL (only when the user asks): `adb forward tcp:9333 localabstract:chrome_devtools_remote`, GET
  `http://127.0.0.1:9333/json/list`, print only the tab asked for, then `adb forward --remove tcp:9333`.
- Google developer verification (enforcement for unregistered sideloaded apps from 2027): register the package under a free limited-distribution account
  (up to 20 devices) or a $25 full account; see https://developer.android.com/developer-verification/guides/faq.
- Release build: `$env:ANTLOGGER_SIGNING_PROPS="E:\jschlager\keys\signing.properties"; .\gradlew.bat assembleRelease`, verify with
  `%ANDROID_HOME%\build-tools\36.0.0\apksigner.bat verify --verbose --print-certs <apk>`; raise `versionCode` per release. Key and password belong to the user.
