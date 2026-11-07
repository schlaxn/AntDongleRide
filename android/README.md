# ANT Logger (Android)

Standalone Android logger for ANT+ sensors through an ANT USB stick on an OTG adapter. No root, no
vendor service: the app talks to the stick directly through the Android USB Host API (a Kotlin port of
`../ant/`). Recording runs in a foreground service, so it survives a locked screen. Raw ANT frames are
always logged; `.fit` export is built from that raw log.

## HARD CONSTRAINT: read-only, never write to sensors
This app only **listens**. It must never transmit anything to a sensor, in particular nothing that can
change a power meter's configuration: no calibration or zero offset, no crank length, no capabilities or
8 Hz / cycling-dynamics "set" commands, no acknowledged or burst data of any kind. Calibration and crank
length are done with the official app (for example the Favero Assioma app when moving the pedals to a new
bike). A wrong write over ANT+ could damage expensive pedals, so there is no write path at all.

How it is enforced:
- The only messages the app sends are the ones that configure the **stick itself** (reset, network key,
  receive channels, search timeouts, extended data, open/close). The whitelist is `Ant.STICK_CONFIG_MSGS`
  in `AntProtocol.kt`.
- Every byte sent to the stick passes through `UsbAnt.write`, which calls `Ant.requireReadOnly` and throws
  for anything else (broadcast, acknowledged, burst, advanced burst, ...). Unit tests in
  `AntProtocolTest.kt` cover this. Do not weaken or bypass it.
- Consequence: the cycling dynamics pages that need a display to enable them (power phase 0xE0/0xE1, PCO and
  rider position 0xE2, torque barycenter 0x14; ANT+ Bicycle Power profile 4.5.2) only arrive if some other
  device has already switched them on. Standard pages (power, balance 0x10, torque effectiveness and pedal
  smoothness 0x13, battery) need no enabling. An earlier version that sent the enable command exists in git
  history (commit b7fda8d) and was removed on purpose.

Status: Phase 1 spike. Verified with a heart-rate watch only. Pedal pages (power, balance,
cycling dynamics) are not yet verified on real hardware.

## What it does
- Opens the ANT USB stick 2 / USB-m (VID 0FCF, PID 1008/1009) and listens on wildcard channels
  (heart rate, power x2, speed and cadence) with infinite search.
- Two pages (swipe or tabs), no scrolling, everything scaled to the display, portrait, true-black theme:
  **Setup** (connect, start/stop recording, settings, device info, raw message log) and **Live** (power,
  heart rate, cadence, balance, power phase, effectiveness, smoothness, PCO, rider position). A banner on
  both pages shows connection, recording time, heart rate, power and speed.
- Recording needs a live heart rate (cadence and speed sensors are optional). Connect and recording are
  separate: stopping a ride keeps the stick connected.
- Raw log per connection: `capture_<time>.csv` with `ts_ms,<raw ANT frame hex>` plus ride start/stop and wheel
  markers, in the app folder `Android/data/app.antlogger/files/`. Per ride: `samples_<time>.csv` with every
  decoded value.
- Watchdog: if the stick restarts itself, channels are re-configured.
- **Automatic .fit export when a recording is stopped**: writes `ride_<time>.fit` (1 Hz records, dropouts left
  as gaps; only fields Garmin supports) and copies it to Downloads. Garmin Connect imports it.
- Screen stays on while the app is open. Optional checkbox "Dim screen when idle" (off by default): after 15 s
  without a touch the brightness drops to a minimum, any touch restores it at once (that first touch only
  wakes the screen and presses nothing). The screen itself never turns off.
- At the next app start, before any connection, a ride that began but was never stopped (app killed, phone
  died) is exported from its raw log, once.

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

## Signed release APK (for sideloading to a friend)
The key and its passwords live OUTSIDE the repo. Never commit or paste the key, the properties file or the
passwords, and do not let an AI assistant create or read the properties file: assistants are shown file
changes automatically, which exposes the password.
1. Create the key once, in your own terminal (it asks for the password itself; keep the `.jks` and the
   password backed up, because updates must be signed with the same key). One password is enough: for the
   default PKCS12 keystore the key password equals the keystore password.
   ```
   New-Item -ItemType Directory -Force E:\jschlager\keys
   & "C:\Program Files\Eclipse Adoptium\jdk-17.0.21.9-hotspot\bin\keytool.exe" -genkeypair -v `
     -keystore E:\jschlager\keys\antlogger-release.jks -alias antlogger -keyalg RSA -keysize 4096 -validity 10000
   ```
2. In your own editor create `E:\jschlager\keys\signing.properties`:
   ```
   storeFile=E:/jschlager/keys/antlogger-release.jks
   storePassword=<password>
   keyAlias=antlogger
   keyPassword=<same password>
   ```
3. Build with the path in the environment variable:
   `$env:ANTLOGGER_SIGNING_PROPS="E:\jschlager\keys\signing.properties"; cd android; gradlew.bat assembleRelease`
   Result: `app/build/outputs/apk/release/app-release.apk`.
   Raise `versionCode` in `app/build.gradle.kts` for every release you hand out.
4. Check the signature: `apksigner verify --print-certs app-release.apk` (in the SDK's build-tools).
Without `keystore.properties` a release build is produced unsigned (`app-release-unsigned.apk`).
Google's developer verification (global from 2027) may require registering the package name and key for
sideloaded apps; see the Android Developer Console, free "limited distribution" account for up to 20 devices.

## Layout
- `AntProtocol.kt` framing and checksum, `UsbAnt.kt` bulk transfer, `AntSession.kt` channels, reader and
  setup threads, watchdog, `RecorderService.kt` foreground service, `Decoder.kt` page decoding and capture
  reader, `FitExporter.kt` .fit writer (official Garmin FIT SDK), `MainActivity.kt` UI.
- Lessons learned: never block the thread that reads the stick while writing to it (the stick's serial
  queue overflows, event 0x34, and it resets itself).
