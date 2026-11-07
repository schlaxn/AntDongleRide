# ANT Logger (Android)

Standalone Android logger for ANT+ sensors through an ANT USB stick on an OTG adapter. No root, no
vendor service: the app talks to the stick directly through the Android USB Host API (a Kotlin port of
`../ant/`). Recording runs in a foreground service, so it survives a locked screen. Raw ANT frames are
always logged; `.fit` export is built from that raw log.

Status: Phase 1 spike. Verified with a heart-rate watch only. Pedal pages (power, balance,
cycling dynamics) are not yet verified on real hardware.

## What it does
- Opens the ANT USB stick 2 / USB-m (VID 0FCF, PID 1008/1009) and listens on wildcard channels
  (heart rate, power x2, speed and cadence) with infinite search.
- Raw log per session: `capture_<time>.csv` with `ts_ms,<raw ANT frame hex>`, in the app folder
  `Android/data/app.antlogger/files/`.
- Watchdog: if the stick restarts itself, channels are re-configured.
- "Export latest capture to .fit": writes `ride_<time>.fit` (1 Hz records, dropouts left as gaps) and copies
  it to Downloads. Garmin Connect imports it.
- Screen stays on while the app is open; optional "Dim screen" setting (off by default).

## Build and install (Windows)
1. JDK 17: `winget install EclipseAdoptium.Temurin.17.JDK`
2. Android SDK command-line tools with `platforms;android-36` and `build-tools;36.0.0`; set `sdk.dir` in
   `android/local.properties` (not committed).
3. `winget install Google.PlatformTools` for `adb`.
4. `cd android && gradlew.bat testDebugUnitTest assembleDebug`
5. Phone: enable Developer options and USB debugging. The dongle needs the phone's only USB port, so
   use Wi-Fi adb: with the cable attached run `adb tcpip 5555`, then `adb connect <phone-ip>:5555`, then
   unplug the cable. (Lasts until the phone reboots.)
6. `adb install -r app/build/outputs/apk/debug/app-debug.apk`

## Layout
- `AntProtocol.kt` framing and checksum, `UsbAnt.kt` bulk transfer, `AntSession.kt` channels, reader and
  setup threads, watchdog, `RecorderService.kt` foreground service, `Decoder.kt` page decoding and capture
  reader, `FitExporter.kt` .fit writer (official Garmin FIT SDK), `MainActivity.kt` UI.
- Lessons learned: never block the thread that reads the stick while writing to it (the stick's serial
  queue overflows, event 0x34, and it resets itself).
