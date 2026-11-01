# AntDongle virtual trainer: handover

Read this first in a new session. It describes the state of the project, how it runs, what is
still open, and how the user likes to work.

## 1. What the project is

A Python (Flask) server plus a browser 3D view that turns a real bike on a trainer into a virtual
ride, with interval workouts, ghost riders, virtual speed/power/gearing, and recordings.

- **Hardware:** ANT USB Stick 2 (VID 0FCF, PID 1008) with the Dynastream libusb-win32 driver
  (`libusb0.dll` in System32). Garmin speed-only sensor (type 123, device 48791), a heart-rate
  broadcast (type 120, device 17691), and the watch over BLE through the laptop's Bluetooth.
- **Trainer:** Elite Qubo Power Fluid (red roller). Power model fixed by two points: 145 W at
  20 km/h (user measurement) and 540 W at 40 km/h (rating). Exponent ~1.90. Checked against the
  manufacturer chart (40 W @ 10, 300 W @ 30, 830 W @ 50, 1200 W @ 60 km/h).
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
python -m unittest discover -s tests    # 82 tests, all passing at handover
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
  numbers: 0 = any, -1 = Bluetooth watch.
- `ble.py`: heart rate from the laptop's Bluetooth adapter via bleak.
- `ride.py`: `RideState` (pure: clock, measured distance, virtual speed and distance via physics,
  virtual gearing, snapshot/row) and `RideEngine` (thread, consumes samples, records, publishes
  ticks). Speed multiplier (testing) is applied here.
- `workout.py`: `Workout` dataclass (warm-up, blocks/reps/on/off, between, cool-down,
  skip_last_recover default True), gradient profile, ghost settings per interval type, ghost
  profiles per level (-10, -5, 0, +5, +10 % of interval power), `to_dict()` for the page.
- `power.py`: trainer power model (`resistance_w`, `speed_for_power`, `power_w`).
- `physics.py`: road physics for virtual speed (mass 76 kg, Crr 0.005, CdA 0.32 m² [guess], rho 1.2).
- `gear.py`: measured gear estimate and cleaning (`clean_rows`), plus virtual gearing: 34/50T front,
  11-34 rear, 13 usable gears (50 ring: 50/27 to 50/17; 34 ring: 34/34 to 34/15), shifts outside
  85-100 rpm, target 92.5 rpm, stays on the 50 ring whenever possible.
- `recorder.py`: writes `recordings/<YYYY-MM-DD_HHMMSS>/` with `ride.csv`, `ride_clean.csv`,
  `samples.csv`, `session.json`.
- `sim.py`: simulated rider.

Page (browser):
- `static/ride.html`: layout (desktop: fixed window height, control panel scrolls; < 900 px: normal
  scroll), HUD tiles, workout panel, ghost controls, sensor dialog, timeline.
- `static/ride/main.js`: page controller (API calls, SSE, start gate, presets, ghost table and mode).
- `static/ride/hud.js`: HUD tiles and timeline drawing.
- `static/ride/world.js`: Three.js 3D world (road, rider rig, ghosts, scenery, camera, sun).
  `buildRig(materials, castShadow)` builds one bike + rider; the ghosts use translucent materials.
- `static/ride/sensors.js`: sensor check dialog.
- `static/ride/audio.js`: beeps.
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
- **Virtual gearing** applies only when there is no real cadence sensor. Cadence = virtual speed
  divided by gear. The gearbox keeps cadence in 75-110 rpm.
- **Ghost rules:** ghost power is the interval power (default: work 27 km/h, recover 18 km/h,
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
  Skip last rec. (checked by default), table (interval | duration | ghost km/h or W), ghost
  checkbox and mode selector, audio cues, gradients (collapsed at the bottom).
- **HUD tile order (on screen):** Power | Heart rate; Cadence | Elapsed; Virtual distance |
  Virtual speed; Gradient | Speed (sensor); Gear ratio (full width).

## 5. Open items (priority order)

1. **Ghost legs and wheels missing** (reported twice, not resolved). Ghosts are built with the same
   code as the rider, with translucent materials. Likely cause: the translucent blue-grey blends
   with the road, so dark parts (tyres, shorts) disappear. Need a screenshot or the user's answer
   about whether this happens only at distance. Do not guess again without a screenshot.
2. **History page missing.** The "History" button links to `/history`, which returns 404. The plan
   is a page that lists rides (`/api/rides`), plots HR, cadence and speed over time with phase
   shading, shows the summary, and offers CSV downloads (`/api/rides/<id>/<file>`).
3. **Not browser-tested recently:** the 3D view after the last rounds (lower airships, reflectors,
   adaptive ghosts, jerseys, the ghost-mode selector). Ask the user to check in the browser first.
4. **Ghost pace settings** (speed/W per interval in the panel) are still used as the starting point
   for parameterized ghosts. Tell the user before changing this.
5. **Recordings** are not yet verified end to end with the virtual values after the latest
   changes (virtual cadence and gear columns were added).
6. **Possible improvements the user has not asked for yet:** a separate speed multiplier input
   instead of a toggle; a README; a `config.json` option for mass and CdA.

## 6. Things that are measured or assumed (confirm, do not invent)

- Rider + bike mass: 76 kg (user).
- CdA 0.32 m² and Crr 0.005: estimates. Tell the user they are guesses if asked.
- Front chainrings: two rings, 34T and 50T (the user's choice; replaces the earlier single 40T ring).
  Rear: 11-34. Only 13 gears are used (see the gear.py comment): 50/30 and 34/14 and smaller are never
  selected. Shift band 85-100 rpm; cadence can exceed 100 rpm above about 38 km/h on 50/17, by design.
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
- Syntax-check the page scripts with `esprima` (ignore `?.`/`??` false positives).
- Do not kill processes other than our own server.
- Do not publish artifacts; this is a local app.
