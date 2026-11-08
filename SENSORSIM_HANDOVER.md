# BLE Sensor Sim (`sensorsim/`): handover

Last updated 8 Nov 2026. Read this first for any work in `sensorsim/`. Build/install workflow is the same as for `android/`
(see `.claude/skills/android-ant-logger/SKILL.md`; adb over Wi-Fi, JDK 17, same Gradle/AGP/Kotlin versions, no AndroidX).

## 1. Goal
An Android app that poses as a **Bluetooth LE FTMS rower** for MyWhoosh on the Windows laptop. Its source is the WaterRower S4
monitor on the phone's USB OTG port. The decoding is a port of the laptop code (`rower/profiles.py`, described in
`ROWER_INTERFACE.md`). The original idea, recording the power on a Garmin watch through the same sensor, did not work (section 4).
Application id `app.sensorsim`, title "BLE Sensor Sim".

## 2. What the app does today
- **Output:** one BLE peripheral (`SensorPeripheral`): GATT server with Fitness Machine (0x1826: Rower Data 0x2AD1, Feature 0x2ACC,
  Control Point 0x2AD9) and Device Information (0x180A), connectable advertisement with FTMS service data (flags 0x01, machine type
  rower = 0x0010). Rower Data (flags 0x082C): stroke rate, stroke count, total distance, pace, power, elapsed time, once a second.
  The control point accepts request control, reset, start/resume, stop/pause and refuses everything else (0x02).
- **Source A, USB rower:** `UsbS4` drives the S4 (CDC serial 04D8:000A, 115200 8N1, DTR/RTS) with the Android USB Host API;
  `RowerLink` reads it on a thread, `S4Decoder` turns lines into a `RowSnapshot` (speed = ticks of the last second / 91.8,
  distance = ticks since the first stroke / 91.8, power per stroke = 1.78 v^3 x fill factor).
- **Source B, manual:** sliders for power, stroke rate and speed (`ManualRower`), for testing without the rower.
- **Hard rule for the monitor:** the only thing ever sent to the S4 is the `USB\r\n` handshake. `UsbS4.write` calls
  `S4.requireHandshakeOnly` (tests in `S4Test`). Do not weaken it. The monitor's fill setting is typed once on the phone (a slider)
  before rowing, never while rowing.
- Foreground service `SensorService` (type connectedDevice, wake lock) runs the BLE side and the 4 Hz ticker.
- Tests: 21 JVM tests (`EncodersTest` 10, `S4Test` 11). Byte layouts are hand-computed from the Bluetooth specs.

## 3. Verified on hardware
- MyWhoosh (on the laptop, 04:D3:B0:FF:EE:47) finds the rower, pairs, reads Feature and Device Information, subscribes to Rower Data and
  the control point, and writes control-point commands (0x00 request control, 0x11 bike simulation, 0x07 start/resume). The control point
  then answered only 0x00 and 0x01 with success. It was extended afterwards (0x07, 0x08), but not yet tested again.
- A generic client (bleak on the laptop) connects, lists all services and receives notifications for every service we offered.
- **Not yet tested:** the USB side (S4 over OTG: permission dialog, handshake, streaming, values in MyWhoosh); MyWhoosh's behaviour after
  the control-point change; the machine-type fix (0x0010 instead of the old wrong 0x0002).

## 4. The Garmin watch: what we tried and what we learned (Forerunner 265)
The watch lists simulated sensors in its search but **never sends a connection request** (checked in the phone's Bluetooth log, tag
`SensorSim` and the stack lines). Tried without effect: power (Cycling Power 0x1818) alone, speed/cadence (CSC 0x1816) alone, both, as
separate advertisements, a Device Information service, requiring pairing (encrypted link), logging every GATT callback, a sensor hosted by
the laptop instead of the phone (the watch did not list it at all). Both the watch and the phone were connected to Garmin Connect during
all tests. The same watch works with real power pedals and generic BLE speed/cadence sensors.
- Garmin publishes no BLE profile requirements. The Forerunner 265 manual lists these sensor types: eBike, Extended Display, External
  Heart Rate, Foot Pod, Headphones, Lights, Power, Radar, RD Pod, **Smart Trainer**, Spd./Cad., Tempe, VIRB. No rowing machine, no general
  fitness equipment.
- Forum reports (unconfirmed, no fixes found): Garmin devices list phone-hosted sensors but never connect; guesses are a public
  address requirement (Android's public API cannot choose the advertising address type), an existing Garmin Connect link, or the Device
  Information content.
- **Untested idea:** pose as an FTMS **indoor bike / smart trainer** (machine type 0x0020, Indoor Bike Data 0x2AD2 with speed, cadence = stroke
  rate, power) so the watch can take it as "Smart Trainer". Needs a control point that accepts the commands a trainer controller sends.
- Not an option: ANT+ (no second ANT receiver on the laptop in production, so the ANT stick cannot be used on the phone).
- Edge 500 cannot be used: it is ANT+ only (to our knowledge).

## 5. Pitfalls found
- The Android GATT stack reports links that already exist (the watch's Garmin Connect link) to every new GATT server at start. The app
  ignores connection events before it advertises, otherwise it shows "1 connected" for no reason.
- Every restart of the advertisement gives a new random address; saved sensors on the other side go stale.
- Two GATT servers in one app share one connection's attribute table, so separate advertisements did not give separate devices for a
  client that connects to both.
- Windows reserves the Device Information service (a laptop-hosted peripheral cannot create it, error 5).
- FTMS machine type is a bit field: rower = bit 4 (0x0010), indoor bike = bit 5 (0x0020). The first version had 0x0002 (cross trainer).
- Installing the app restarts it: the sliders and the USB checkbox come back from `Hub` defaults and the saved settings (USB checkbox and
  fill level are saved, the manual values are not).

## 6. Next steps (in order)
1. Rower test: plug the S4 into the phone with the OTG adapter, tick "Use the USB rower", start, allow the USB dialog, row a few strokes;
   check the status line ("USB rower: streaming") and the values in the app, then in MyWhoosh.
2. Check what MyWhoosh does with the rower data (does it draw strokes, pace, power?) and whether it needs more FTMS (status
   characteristic, training status).
3. Watch: try the equipment/smart-trainer route on the watch; if only "Smart Trainer" is offered, build the indoor-bike profile as a
   selectable device type.
4. Keep this file and `ROWER_HANDOVER.md` current. `sensorsim/` is not committed yet.
