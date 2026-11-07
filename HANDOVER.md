# AntDongle virtual trainer: handover

Read this first in a new session. It describes the state of the project, how it runs, what is
still open, and how the user likes to work.

The Android ANT+ logger (`android/`) and the rowing app have their own handovers:
`ANDROID_HANDOVER.md` and `ROWER_HANDOVER.md`.

## 1. What the project is

A Python (Flask) server plus a browser 3D view that turns a real bike on a trainer into a virtual
ride, with interval workouts, ghost riders, virtual speed/power/gearing, and recordings.

- **Hardware:** ANT USB Stick 2 (VID 0FCF, PID 1008) with the Dynastream libusb-win32 driver
  (`libusb0.dll` in System32). Combined speed & cadence sensor (type 121, device 53894, used for
  speed and cadence), an older Garmin speed-only sensor (type 123, device 48791), a heart-rate
  broadcast (type 120, device 17691), and the watch over BLE through the laptop's Bluetooth.
- **Trainer:** Elite Qubo Power Fluid (red roller). Power model fitted to power measured with
  Favero Assioma pedals (7 Nov 2026, settled state after ~10-15 min): 105 W at 20 km/h, exponent
  1.95 (R2 0.99, measured over 14-32 km/h; the 406 W at 40 km/h is extrapolated). It replaced
  the first calibration (145 W at 20 km/h, 540 W at 40 km/h, exponent 1.90), which overestimated
  the measured power by 27-39 %; the manufacturer chart (300 W at 30 km/h) is also ~23 % above
  the measurement. In the first ~10 minutes the same speed took ~20 % less power (83 W at 20 km/h).
- **Rider:** 184 cm, 91 cm inseam, system weight 76 kg (rider + bike).
- **Bike:** Orbea Orca, 57 cm frame, 73°/73° head and seat tube, 58 cm effective top tube (slight
  slope up towards the head tube), 810 mm saddle height (measured along the seat tube from the
  BB), 170 mm cranks, 28" x 30 mm tyres (circumference 2.105 m), 100 mm stem, 75 mm reach,
  125 mm drop. Chainstay 405 mm and wheelbase 1010 mm are estimates.

## 2. How to run

```
cd E:\jschlager\Documents\Projects\AntDongle
python app.py                 # real sensors, http://127.0.0.1:5000
python app.py --simulate      # simulated rider (fake HR/speed/cadence)
python app.py --simulate --no-cadence
python diag.py                # stage-by-stage stick and sensor check
python -m unittest discover -s tests    # 97 tests, all passing at last update
```

- **Server changes need a restart** (stop the process listening on port 5000, then start again).
- **Static changes** (`static/**`) only need a reload with **Ctrl+F5**.
- Stopping the server: `Get-NetTCPConnection -LocalPort 5000 -State Listen`, then `Stop-Process`.
  Only do this for our own server process.
- The browser test attempts (headless Edge) hung earlier. Do not use them to kill browser
  processes; the user may have their own windows open.
- Python 3.11, packages installed: pyusb, flask, bleak, numpy, matplotlib, pillow, esprima (only
  for syntax checks; it cannot parse `?.` / `??`, so its "Unexpected token" on those is a false
  positive).

## 3. Code map

Server (Python):
- `app.py`: Flask routes. Connect/scan/sensors, workout, ride control (`/api/ride/*`), config,
  recordings (`/api/rides`), speed multiplier test endpoint (`/api/test/speed-multiplier`), and
  the SSE stream (`/api/stream`). Pages: `/` (debug UI), `/ride`, `/history` (**does not exist yet**).
- `ant/usbdev.py`, `ant/protocol.py`, `ant/session.py`, `ant/profiles.py`: ANT USB stack. Session
  owns the stick, reader thread, channel pairing (`search`, `pair`, `close_search`, MAX 8 channels),
  decoded samples published to subscribers.
- `sensors.py`: sensor roles (heart rate, speed, cadence) and which device feeds each. Device
  numbers: 0 = any, -1 = Bluetooth watch. Speed accepts types 121 and 123; cadence accepts 121
  and 122. A combined sensor (121) fills both speed and cadence.
- `ble.py`: heart rate from the laptop's Bluetooth adapter via bleak.
- `ride.py`: `RideState` (pure: clock, measured distance, virtual speed and distance via physics,
  virtual gearing, snapshot/row) and `RideEngine` (thread, consumes samples, records, publishes
  ticks). `RideEngine._combined_live()` says whether a combined sensor feeding speed or cadence is
  transmitting (last packet within 3 s); that makes a quiet cadence read 0, not virtual. Speed
  multiplier (testing) is applied here.
- `bots.py`: other riders on the road (replaces the ghost riders). `Traffic` holds the schedule
  (8 encounters per minute, testing rate, `RATE_PER_MIN`), the active riders (`MAX_ACTIVE` = 60) and
  the counts `met`, `passed`, `passed_by`. About 60 % of encounters start ahead (the rider passes them),
  40 % behind (they pass the rider). Archetypes (Attacker, Diesel, Climber, Rouleur, Fader, Sprinter)
  each ride on the rider's own capability per interval kind.
- `workout.py`: `Workout` dataclass (warm-up, blocks/reps/on/off, between, cool-down,
  skip_last_recover default True: the last rep of every block skips its recovery, and the
  between-block rest or the cool-down follows), gradient profile, `to_dict()` for the page.
  Default `3-13-30-15` = 59:00 with skipping on. The ghost riders were removed.
- `power.py`: trainer power model (`resistance_w`, `speed_for_power`, `power_w`).
- `physics.py`: road physics for virtual speed (mass 76 kg, Crr 0.005, CdA 0.32 m² [guess], rho 1.2).
- `gear.py`: measured gear estimate and cleaning (`clean_rows`), plus virtual gearing: 34/50T front,
  10-speed cassette 11-12-13-14-15-17-19-21-24-28 (confirmed by the user), 16 usable gears (34 ring:
  34/28 to 34/13; 50 ring: 50/21 to 50/11). The shift ladders are `UP_LADDER` and `DOWN_LADDER`,
  walked one gear at a time. Shifts keep cadence in 85-100 rpm.
  Cross-chain rule: 50/24 and 50/28 (two largest cogs) and 34/11 and 34/12 (two smallest) are never used.
- `recorder.py`: writes `recordings/<YYYY-MM-DD_HHMMSS>/` with `ride.csv`, `ride_clean.csv`,
  `samples.csv`, `session.json`.
- `sim.py`: simulated rider.

Page (browser):
- `static/ride.html`: layout (desktop: fixed window height, control panel scrolls; < 900 px: normal
  scroll), HUD tiles, workout panel, "show other riders" checkbox, sensor dialog, timeline.
- `static/ride/main.js`: page controller (API calls, SSE, start gate, presets, other-riders checkbox).
- `static/ride/hud.js`: HUD tiles and timeline drawing.
- `static/ride/world.js`: Three.js 3D world (road, rider rig, other riders with name tags, scenery, camera, sun).
  `buildRig(materials, castShadow)` builds one bike + rider; the other riders use opaque pooled rigs.
- `static/ride/sensors.js`: sensor check dialog. Speed Search covers types 121 and 123; cadence
  Search covers 121. Choosing a combined sensor for speed or cadence also sets the other role to
  the same sensor, when that role is still on type 121 or "Any".
- `static/ride/audio.js`: beeps.
- `static/ride/main.js` also keeps the screen awake while riding, or while the speed sensor is
  live, with the Screen Wake Lock API (`keepAwake`, re-requested when the page becomes visible).
- `static/vendor/three.module.min.js`: Three.js r170, pinned, local.
- `static/index.html`: the debug UI (packets, graphs, BLE scan).

Other:
- `physics.py`, `workout.py`, `ride.py`, `gear.py` are unit-tested in `tests/`.
- `HANDOVER.md` (this file).

## 4. Key design decisions (keep consistent)

- **Two speeds:** *measured* speed = sensor reading. Used for: workout parameters, the ghost's
  parameterization, and the speed multiplier test. *Virtual* speed = physics on the road from the
  power the trainer absorbs at the measured speed. The animation and the virtual distance follow
  virtual speed.
- **Virtual gearing** applies only when there is no cadence source. A live combined sensor (type
  121) is a cadence source: when it reports no cadence (crank stopped), cadence is 0, not virtual.
  Virtual cadence = virtual speed divided by gear. The gearbox keeps cadence in 85-100 rpm
  (`CAD_LO`, `CAD_HI` in `gear.py`).
- **Bike roll while pedalling:** `ROCK_RAD` in `static/ride/world.js` (0.0122 rad = 0.7°). The bike
  rolls once per crank turn, with the sign flipped once after it ran the wrong way. Set it to 0 to
  turn it off. Only the rider's bike rolls; the ghosts do not.
- **Other riders (replaces the ghost riders):** server-side in `bots.py`, listed in the snapshot as
  `bots` and `bot_counts`. The browser draws them on opaque pooled rigs with name tags, and shows
  "You passed X" / "X passes you" toasts. The "show other riders" checkbox is remembered in the browser.
  Riders keep their pace during pauses (the ride time freezes). The old ghost rules below no longer apply.
- **Old ghost rules (removed):** ghost power is the interval power (default: work 27 km/h, recover 18 km/h,
  others 18 km/h, converted to power on the trainer curve). Ghosts spawn at random, just outside
  the view, ahead or behind, in lateral slots 0.5 m apart (x = -2.25 .. -0.75 and 0.75, 1.25; never
  on the centre marker at x = 0). The rider is at x = +2.5 m, 1 m in from the right edge line at +3.5.
  The stream spawns every 4–10 s (raised from 12–37 s at the user's request).
  - *Parameterized:* ghost follows its level profile.
  - *Adaptive speed:* ghost speed = (1 + level) × your average virtual speed over 60 s.
  - *Adaptive power:* ghost speed = (1 + level)^(1/1.9) × your average virtual speed.
  - Jerseys: +10 yellow, +5 pink, 0 red, -5 white, -10 green (translucent ghost body).
- **Scenery:** hot-air balloons (kept), airships (lower, 45-63 m), windmills, birds, clouds,
  trees, fence posts (white, with red reflectors on the right side and white on the left), a white
  centre line and white edge lines on the road.
- **Workout panel order:** Load workout (top), Preset (applies on change), Blocks, Reps,
  Skip last rec. (checked by default), table (interval | duration), other-riders checkbox, ghost
  checkbox and mode selector, audio cues, gradients (collapsed at the bottom).
- **HUD tile order (on screen):** Power | Heart rate; Cadence | Elapsed; Virtual distance |
  Virtual speed; Gradient | Speed (sensor); Gear ratio (full width).

## 5. Open items (priority order)

1. **Other riders: check in the browser.** The bots use opaque materials, which should fix the
   old ghost legs and wheels problem, but this is unverified. The user's real 54-minute test also
   shows whether the 60/40 split and the riders' pace feel right.
2. **History page missing.** The "History" button links to `/history`, which returns 404. The plan
   is a page that lists rides (`/api/rides`), plots HR, cadence and speed over time with phase
   shading, shows the summary, and offers CSV downloads (`/api/rides/<id>/<file>`).
3. **Not browser-tested recently:** the 3D view after the last rounds (lower airships, reflectors,
   adaptive ghosts, jerseys, the ghost-mode selector, the bike roll, the wake lock). Ask the user to
   check in the browser first.
4. **Ghost pace settings** (speed/W per interval in the panel) are still used as the starting point
   for parameterized ghosts. Tell the user before changing this.
5. **Recording report is not in the repo.** The HTML report generator (zoomable charts, gear
   distribution, phase power averages) lives in the Claude scratchpad, not in the project. Decide
   whether to move it into the repo (for example `tools/make_report.py`).
6. **Stick stops responding after a forced process kill.** After `Stop-Process -Force` on the server,
   the stick can time out on every USB write (`libusb0-dll:err [_usb_reap_async] timeout error`), and
   `/api/sensors/search` returns HTTP 500. Fix: unplug and replug the ANT stick, then Connect and
   Search again. The 500 is a bare error page; a clear message for this case is still open.
7. **Gear estimate for the report** uses the 34 ring only (no chainring change in the ride) and the
   nearest 34 gear to the measured ratio, median-filtered, with at most 2 cogs per change. Measured
   ratio peaks match 34/19, 34/17, 34/15, 34/14 and 34/13 within about 0.5%, so the wheel
   circumference (2.105 m) and the ring are consistent with the data. The first block was 34/14 for
   about 6 min; that was only detected after 14 was added to the cassette.
8. **Slip** was mentioned by the user on 34/15 but not quantified. The 34/15 peak agrees with the
   nominal ratio within about 0.6%, so the recorded data showed almost no slip. Ask for an amount if
   it matters.
9. **Possible improvements the user has not asked for yet:** a separate speed multiplier input
   instead of a toggle; a README; a `config.json` option for mass and CdA.

## 6. Things that are measured or assumed (confirm, do not invent)

- Rider + bike mass: 76 kg (user).
- CdA 0.32 m² and Crr 0.005: estimates. Tell the user they are guesses if asked.
- Front chainrings: two rings, 34T and 50T (the user's choice; replaces the earlier single 40T ring).
  Rear: 10-speed 11-12-13-14-15-17-19-21-24-28, confirmed by the user. The 16 gears and the
  cross-chain exclusions are in the gear.py comment. Shift band 85-100 rpm; cadence can exceed
  100 rpm above about 38 km/h on 50/17, by design. Real rides used 34 only (no chainring change).
- Workout `3-13-30-15`: 3 blocks, 13 reps of 30 s on / 15 s off, 5 min between blocks, 10 min
  warm-up and cool-down. The user's own FTP is estimated at 3.5 W/kg x 66 kg = 231 W (not measured).
- Chainstay 405 mm and wheelbase 1010 mm: estimates from the user's wheelbase statement.
- The speed multiplier is currently ×5 at runtime, set through the API. It resets to ×1 on restart.
  The page button toggles ×1 and ×5.

## 7. How the user works (preferences)

- Gives short, precise feedback ("the legs are wrong", "move the drops back by 3 cm, keep the
  hands"). Make exactly the change asked. Do not change nearby things unasked, and say so if a
  change is needed elsewhere.
- Compares the 3D model against real photos (bike photo, handlebar photos). When geometry is in
  question, draw a side view from the same numbers and compare, instead of guessing again.
- Gives physical measurements (rider height, inseam, bike sizes, mass). Use them rather than
  generic values.
- Prefers layout and UI requests stated as precise positions (e.g., "row 3 and row 4"). Apply them
  literally and check the order.
- Dislikes repeated guessing. After two failed attempts at the same problem, ask for a screenshot
  or a specific measurement.
- Writes in English with German bike terms (Oberlenker = top bar, Unterlenker = drops, Vorbau =
  stem, Bremsgriffposition = hood position).
- Testing with a faked speed sensor (×5 multiplier), so virtual values differ from the sensor.
- Wants the ghost challenge to stay at the workout's interval power until enough own recordings
  exist.

## 8. Working rules for the next session

- Before editing, read the current file region (the file changes often).
- Keep the unit tests green: `python -m unittest discover -s tests`.
- After server changes, restart the server. After page changes, tell the user to press Ctrl+F5.
  After a server restart, reconnect the stick (`POST /api/connect`), then start the combined sensor
  search (`POST /api/sensors/search` with `{"devtype": 121}`).
- Git: origin is `github.com/schlaxn/AntDongleRide`. The user has asked for direct commits and
  pushes to `main`, not feature branches.
- Syntax-check the page scripts with `esprima` (ignore `?.`/`??` false positives).
- Do not kill processes other than our own server.
- Do not publish artifacts; this is a local app.
