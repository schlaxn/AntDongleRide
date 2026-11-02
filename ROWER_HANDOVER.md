# WaterRower S4 handover

Status as of 2 Nov 2026. The rower work is separate from the ANT+ ride app, which is documented in `HANDOVER.md`.

## 1. Goal

Read a WaterRower S4 monitor over USB on this laptop and turn its data into live power, speed and stroke rate for a virtual boat. Phase 0 (raw traffic capture) is done. Phase 1 (decoding) is in progress.

## 2. Hardware and link

- Port: `COM6`, "Serielles USB-Gerät", VID `04D8` PID `000A`, Microsoft driver.
- Settings: 115200 baud, 8N1. This is the first rate tried and it works.
- Handshake: `USB\r\n` gets a reply (12 to 18 bytes). This was a guess from community code and it works.
- The port sometimes refuses to open with `PermissionError(13, 'A device attached to the system is not functioning')`. Retrying a few minutes later worked each time. The cause is unknown.
- The monitor sends a stream of ASCII lines. Nothing is sent when the flywheel is still, apart from a `PING` once per second.

## 3. What the stream contains

| Line | Meaning | Evidence |
|---|---|---|
| `SS` / `SE` | Stroke start / end | One pair per stroke. Marker counts match the monitor's stroke rate within about 2/min. |
| `P01`..`P10` (hex, so `P0A` = 10, `P10` = 16) | A speed-like code | Rises smoothly with effort. About 40 lines per second, steady in every capture. |
| `PING` | Keepalive | Once per second. |
| `AKR` | Unknown | Seen once at startup. |

What is ruled out:
- One line per physical pulse. The line rate is fixed (about 40/s) regardless of load, so the lines are a periodic status report.
- The code as watts directly. The fitted exponent against watts is about 0.35, not 1.
- The code as inverse pulse period. The code only rises with effort.

The unit behind the code is not identified. Land mph gives a constant near 1.04–1.08 but predicts worse when forced to 1. Feet, knots and m/s don't give a clean constant. The working approach is an empirical constant (section 5), not a physical unit.

## 4. Calibration data

All code values are decimal means over steady windows.

### 17 L (calibration used to fit 0.143)

| Load (monitor) | Mean code | Note |
|---|---|---|
| 0 W | 2.19 | 21 revolutions counted by hand in 30 s |
| under 20 W | 2.35 | 24 revolutions counted by hand in 30 s |
| 45 W | 6.67 | about 60 revolutions (rough estimate) |
| 66 W | 7.67 | |
| 105 W | 9.10 | |
| 120 W | 9.36 | |
| 180 W | 11.00 | |
| 300 W | 12.89 | short pulse |
| 150 W ramp | about 10.2 | end anchor, lower confidence |

Fits:
- Free fit: code ≈ 1.775 × W^0.349 (R² = 0.9986).
- Forced exponent 1/3: W ≈ 0.143 × code³. Checked against the points above, within ±6%.
- Ramp (50 to 150 W, restricted to 65 W and up, offset 39 s): exponent 0.332, close to 1/3.

### 19 L (same rower, fill level changed, resistance unchanged)

The 19 L calibration is set in the monitor, not by refilling. The physical resistance is the same.

| Load (monitor) | Window | Mean code | Ratio (monitor / 17 L model), 25 s window |
|---|---|---|---|
| 100 W | 30 s steady, ending 17:55:49 | 8.60 | 1.12 |
| 121 W | 30 s steady, ending 17:57:53 | 9.19 | 1.11 |
| 164 W | ramp, reading at 17:59:41 | 10.38 (last 10 s) | 1.09 |
| 149 W | dropped, see below | 10.10 | — |

The 19 L factor is about **1.11** on the three runs kept. The 149 W run was dropped because the initial monitor readings for it were never given, so it may include start-up.

## 5. Current formulas

Implemented in `rower/profiles.py` (the speed is the primary measurement, see section 5a):

- `v [m/s] = code / CODE_PER_MS`, with `CODE_PER_MS = 2.326`. This is the empirical scale from the 17 L monitor readings, not a physical unit.
- `W = WATT_CONST × v³`, with `WATT_CONST = 1.78`. This is the user's cube law at 17.0 L. Power is derived from the speed.
- At 19 L the displayed power is multiplied by `FILL_POWER_FACTOR[19] = 1.07`. The speed is not affected.
- `split = 500 / v` seconds, shown in whole seconds.

## 5a. Architecture: measured versus derived

For rowing the speed is the primary measurement. The paddle moves the flywheel in the water tank, and the monitor reports how fast it goes. Power is not measured. It is derived from the speed with the cube law. The app therefore does the following:

- **Motion, distance and split** follow the measured speed. The view moves with it, and the distance is the running total of the measured speed, so it matches the monitor's distance.
- **Power** is derived from the speed: `W = 1.78 × v³`. The display averages it over a window (see the smoothing below).
- **The virtual boat** (`BoatPhysics`) is not used for the rower. The real tank already gives the real inertia and acceleration, so a second, modelled boat would only add error. The rower's virtual distance and speed were removed for that reason.
- **Other boats** (the dolphins) still use `BoatPhysics` for their own motion, since they have no measured speed.

For biking the split is the opposite. Power is the primary measurement, from the static dyno (or a direct power meter), and the speed is virtual: it is calculated from that power and the gradients, the rider's mass and the drag. The rowing and biking apps therefore differ in which quantity is measured and which is derived, which is why the rowing app does not reuse the bike's virtual speed model.

The displayed speed, split and power are smoothed. Steady efforts show a 10-second average. A deliberate change (more than 10% for 1.5 s) shows the 2-second average instead, so intervals appear within about 2–3 seconds (`display()` in `rower/engine.py`). The physics and the distance use the raw values.

## 6. Open questions

1. **Averaging window.** The monitor appears to average over about 25 seconds. The 164 W ramp supports this. A clean step test would confirm it.
2. **Linear fill effect.** Only two fill levels (17 and 19 L) have been tested, so linearity cannot be checked. The liter effect is a factor of about 1.11 at 19 L.
3. **The 149 W run.** It was dropped. Re-run it with initial readings recorded, or accept the drop.
4. **Code scale.** The highest code seen is `P10` (16) at 300 W. Harder efforts may go higher.
5. **Unit behind the code.** Unresolved. Not needed for the formula.

## 7. Files

Phase 0 (working and tested):
- `rower/__init__.py`, `rower/serialdev.py`: serial port, reader thread, pub/sub queue. Same pattern as `ant/session.py`.
- `rower/web.py`: Flask blueprint for `/rower-debug` and `/api/rower/*`. Registered in `app.py`.
- `static/rower_debug.html`: debug page with port picker, live log, send box, save capture.
- `rower_diag.py`: command-line check (enumerate, open, handshake, listen). Usage: `python rower_diag.py --port COM6 --baud 115200 --listen 60`.
- `tests/test_rower_serial.py`: unit tests with a fake serial port.
- `requirements.txt`: `pyserial>=3.5` added.

Phase 1 (written, not yet tested or wired in):
- `rower/protocol.py`: classifies each line into `SS`, `SE`, `PING`, `P0x` (with decimal value) or `OTHER`.
- `rower/profiles.py`: stateful `Decoder` turning `P0x` codes into power, speed, split and stroke rate. It uses a 20-line rolling mean for the code.

Captures (session scratch folder, temporary; copy them into the repo if they need to be kept):
`C:\Users\JSCHLA~1\AppData\Local\Temp\claude\E--jschlager-Documents-Projects-AntDongle\5a08b3f1-a633-4282-bab1-e213783d4dc5\scratchpad\`
- `rev_test2.txt`, `rev_test3.txt` (0 W and under 20 W, hand-counted revolutions)
- `rev_test5.txt` (180 W), `pulse_test1.txt` (300 W)
- `ramp_test1.txt` (50 to 150 W ramp)
- `level19_test3.txt` (149 W, dropped), `level19_test5.txt` (100 W, 19 L), `level19_test6.txt` (121 W, 19 L), `level19_test7.txt` (164 W, 19 L)

Nothing has been committed to git yet.

## 8. Next steps

1. Write unit tests for `rower/protocol.py` and `rower/profiles.py`, using lines from the captures above.
2. Wire the `Decoder` into `RowerSerial`'s reader, so samples (`power`, `speed`, `split_500m`, `stroke_rate`) are published on the same queue as the ANT samples.
3. Add a `rower_fill_litres` setting, and apply the 19 L factor (about 1.11) when set.
4. Add a rower HUD page, reusing the ride app's HUD where possible.
5. Confirm the averaging window with a sharp step test, when the user is rowing again.

## 8b. Rowing app (built, status)

Run with `python row_app.py --simulate` (no hardware) or `python row_app.py --serial COM6`. Open http://127.0.0.1:5001/row. The ride app on port 5000 is untouched.

Built and tested (156 unit tests pass):
- `rower/hub.py` (sample hub), `rower/source.py` (serial to samples), `rower/profiles.py` (decoder, now with devtype/devnum and stroke count samples), `rower/boat.py` (drag c·v², steady state W = c·v³), `rower/roles.py`, `rower/engine.py` (RowState/RowEngine: state machine, finalizes a session exactly once), `rower/recorder.py` (recordings/rows/<stamp>/), `rower/sim.py` (simulated rower), `rower/api.py` (routes under /api/row/*), `row_app.py`.
- Browser: `static/row.html`, `static/row/main.js` (controller, countdown, audio cues), `hud.js` (tiles, timeline), `sensors.js` (S4 port and Bluetooth heart rate), `world.js` (river, banks, instanced trees, bridges, sailing boats, ducks, animated scull and rower).

Not yet built (from the plan):
- Bots (other boats), race mode and the Thames landmark table.
- Wake, puddles, reeds, boathouses, swans, geese, herons, towpath runners, dinghy tacking.
- Quality setting and the day cycle.
- The browser JavaScript has not been run in a browser yet (node is not installed here, so no syntax check). Check the page first.
- Calibration: fill 15 L uses factor 1.00 until the measurement series is done.

## 8a. Ideas for later

- **Android sensor.** Run the decoder on an Android phone and publish power and speed over Bluetooth LE as a cycling-power sensor, so the rower can feed apps directly. The phone would read the monitor's USB serial stream through USB host mode, for example with usb-serial-for-android. Not tested.
- **Why this could be better.** The decoded speed is a raw signal, not the monitor's averaged, displayed values. Its advantage depends on the code being a direct flywheel measurement, which is not yet confirmed.

## 9. Working rules for this project

- The user reads the monitor's watts and the window-end time. Ask for the window-end time from the timer page before computing anything.
- Hand-counted revolutions are only reliable below about 40 per 30 seconds.
- Don't ask for more rowing without a reason. The user has said they are done for the day.
- Don't publish artifacts. The timer and counter pages are local files in the scratch folder.
