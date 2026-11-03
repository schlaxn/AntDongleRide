# WaterRower S4 handover

Status as of 3 Nov 2026. The rower work is separate from the ANT+ ride app, which is documented in `HANDOVER.md`. **The interface is now reverse engineered. The clean description is in `ROWER_INTERFACE.md`; this file keeps the log of how each finding was reached.** Where the two differ, `ROWER_INTERFACE.md` is newer (sections 5f and 5g below summarise the last findings).

## 1. Goal

Read a WaterRower S4 monitor over USB on this laptop and turn its data into live power, speed and stroke rate for a virtual boat. Phase 0 (raw traffic capture) is done. Phase 1 (decoding) is in progress.

## 2. Hardware and link

- Port: `COM6`, "Serielles USB-Gerät", VID `04D8` PID `000A`, Microsoft driver.
- Settings: 115200 baud, 8N1. This is the first rate tried and it works.
- Handshake: `USB\r\n` gets a reply (12 to 18 bytes). This was a guess from community code and it works.
- The port sometimes refuses to open with `PermissionError(13, 'A device attached to the system is not functioning')`. Retrying a few minutes later worked each time. The cause is unknown.
- The monitor sends a stream of ASCII lines. Nothing is sent when the flywheel is still, apart from a `PING` once per second. (Corrected in 5f: `P0x` lines are sent only when at least one tick was counted in the 25 ms period.)

## 3. What the stream contains

| Line | Meaning | Evidence |
|---|---|---|
| `SS` / `SE` | Stroke start / end | One pair per stroke. Marker counts match the monitor's stroke rate within about 2/min. |
| `P01`..`P10` (hex, so `P0A` = 10, `P10` = 16) | A tick count: ring segments in the last 25 ms | Rises smoothly with effort. About 40 lines per second while rowing, but sparse in a slow coast (5f). |
| `PING` | Keepalive | Once per second. |
| `AKR` | Unknown | Seen once at startup. |

What is ruled out:
- One line per physical pulse. The line rate is fixed (about 40/s) while rowing, regardless of load, so the lines are a periodic status report of the ticks counted in the last period (a period with no tick sends no line).
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

## 5b. Working model (3 Nov 2026): the code is a tick count

Working hypothesis: each `P0x` line carries the number of ring ticks counted in the last period, so the code is proportional to flywheel speed. Not proven yet, but the data so far fit it.

Evidence:
- **Spin-down.** After the last stroke (session `recordings/rows/cal_2026-11-03_084521_17L`, t = 225 s) the code falls smoothly from about 8 to 2.2 over 10 s and never rises. Values stay integers (2 to 9). An earlier note in this file said the code rose during stops; that was the monitor's averaged m/s, not the code.
- **Proportional to speed.** Steady phases at 3.8 m/s (100 W, code 8.74 per line) and 3.2 m/s (60 W, code about 7.4) both give code / v = 2.31 per m/s.
- **Stroke-to-stroke jitter.** At a steady 100 W, the 36 stroke-cycle means have sd 0.22 (2.5 %). Per line the sd is 1.03, far above the 0.29 that integer rounding alone would give, so tick timing within a line varies strongly.
- **Low-speed hand counts.** 21 revolutions in 30 s at 0 W (code 2.19) gives 3.13 code per rev/s. 24 revolutions in 30 s under 20 W (code 2.35) gives 2.94. Mean about 3.0.

Ticks per revolution: code per (rev/s) = N x (seconds per line), so N = 3.0 x line rate. At 35 to 40 lines/s, N = 105 to 120 ticks per revolution. This session measured 40 lines/s, so N is about 120. Self-check: at 0.7 rev/s and N = 120, a 40/s line carries 2.1 ticks, which matches the 2.19 measured.

If the model holds:
- Speed = code / 2.31 (code per m/s). This is the calibration constant, and the 1.78 cube law stays a drag constant per fill level.
- At 19 L the same speed gives the same code but more watts, which fits the 19 L table (ratio about 1.11).
- Distance from integrated speed at 2.31 is about 778 m for the 814 m monitor session, a gap of about 4.5 %. Not explained yet (boat model or session boundaries).

Open:
1. Check N with a counted interval at higher speed (a constant-speed flywheel count by hand).
2. Power check with monitor watts at steady phases: v = code / 2.31, c = W / v^3. At 98 W, c = 1.77. At about 60 W, c = 1.77 to 1.89 depending on the exact watts. 1.78 is consistent.
3. Steady phases at 15 L and 19 L to get the drag ratio.
4. Lock detection: the 60 W phase did not lock at any band from 2 % to 5 % with 5 s or 10 s holds. Stroke-to-stroke noise is wider than the band, so the detector needs a rethink.

### Conclusions (3 Nov, later)

- **The stream is raw ring-segment counts.** Most likely each `P0x` line is the number of ring segments counted in the last period, with no further decoding needed. The 120 segments per revolution is an assumption and is not critical.
- **Two conversion factors matter.** Both are ring segments per interval, one per quantity:
  - speed: `v = ticks / K_v`, where K_v is about 2.31 ticks per line per m/s at 17 L (fitted from the steady phases);
  - power: `W = c * v^3`, with `v = ticks / K_v` and c = 1.78 at 17 L. The power is therefore `W = c * (ticks / K_v)^3`, and the cube law is already known.
- **Resolution.** One tick in about 600 per stroke is about 0.17 % in speed and about 0.5 % in power, which is finer than the monitor's 1 W steps. The stroke-to-stroke spread at a steady 100 W is 2.5 %, so it comes from the real stroke, not from counting.
- **Blade ripple.** The 6 Hz blade-pass ripple was a hypothesis and is not in the data. The 5.34 Hz peak in the steady 100 W phase is unexplained.

## 5c. Test plan: confirm the tick model (for later)

Goal: confirm three things. (1) The `P0x` value is a raw ring-segment count per line. (2) The ticks-to-speed factor K_v does not depend on the fill level. (3) Only the watts-per-speed constant c changes with the tank, following W = c * v^3.

Setup:
- Fill levels 15 L, 17 L and 19 L. Change the level only while stopped.
- Demand 100 W on the monitor, using the same speed demand as the calibration page (3.8 m/s). Hold it for at least 2 minutes per level, with the natural stroke rate (about 34 strokes per minute).
- Record with the calibration page, which logs every sample from Start to Abort. Press Abort after the last stroke.
- No values are entered while rowing. After each phase, write the monitor's watts and pace in a notes file.

Predictions if the hypothesis holds:
- P1: at a fixed 100 W on the monitor, ticks per line change with the fill level. Expected 19 L / 17 L ratio: (1 / 1.11)^(1/3) = 0.966 using the measured factor, or (1 / 1.07)^(1/3) = 0.978 using the current code factor.
- P2: at a fixed speed (the same ticks per line), the monitor's watts scale with c. Expected W(19 L) / W(17 L) = 1.11 (or 1.07).
- P3: ticks per stroke show the same stroke-to-stroke spread at every level (about 2.5 % at 17 L).
- P4 (optional): ticks per stroke divided by revolutions per stroke gives N, the ring segments per revolution.

Analysis:
- Take K_v from the 17 L phase, using the pace reference or the cube-law speed.
- For each level, compute v_i = ticks_i / K_v and c_i = W_i / v_i^3, then compare the ratios c_i / c_17 with P1 and P2.
- A monitor watt reading is good to about 1 W, so c is good to about 1 % at 100 W.

Pass: the ticks ratio (P1) and the watts ratio at equal ticks (P2) both match their predictions within about 1 %. Any other pattern means the raw value is not a speed-only count, and the model must be revisited. A short step test at 60 W can follow once 100 W is confirmed.

The earlier level series in `rower/measurements/level_series_protocol.md` (private, not in git) covers similar ground. Reuse its run order and notes format.

## 5d. Fill-level runs (3 Nov 2026)

All runs: the calibration page (`/row-cal`), demand 100 W on the monitor (3.8 m/s), recording from Start to Abort. Recordings in `recordings/rows/`.

| Fill | Recording | Lock | Steady code per line | Monitor distance | Integrated (k = 2.326) | Monitor / integrated |
|---|---|---|---|---|---|---|
| 17 L | `cal_2026-11-03_083120_17L` | 100 W, locked at 73 s, code 8.698, k 2.289 | | | | |
| 17 L | `cal_2026-11-03_084521_17L` | 100 W, locked at 105 s, code 8.777, k 2.310; 60 W phase skipped | about 8.8 | 814 m | 772 m (first to last stroke) | 1.054 |
| 15 L | `cal_2026-11-03_101617_15L` | 101 W, locked at 89.5 s, code 9.13, k 2.403 | about 9.1 | 374 m | 358 m (all samples) | 1.045 |
| 19 L | `cal_2026-11-03_101931_19L` | no lock (98 to 102 W, 100 W at end) | about 8.6 | 470 m | 464 m (all samples) | 1.012 |

Derived from the steady codes at equal watts (code is proportional to (W / c)^(1/3)):
- c15 / c17 ≈ 0.90 (confirmed on the monitor, see 5e; the code now has 0.887)
- c19 / c17 ≈ 1.07 from an unlocked run. The monitor reading in 5e gives 1.09 to 1.10 instead, so the 1.07 was the doubtful one.

Caveats: one run per level, offline numbers only (19 L did not lock), monitor watts are whole watts, and steady windows vary by a few percent. The monitor distance gap is 5.4 %, 4.5 % and 1.2 %, which does not yet show a clear fill pattern.

Physics check (corrected): the physical tank is the same at every setting, so the drag is the same. The fill setting only changes the watts the monitor displays. The ticks give the real flywheel speed, and the actual power is 1.78 * v^3 with the physical constant. Taking 17 L as the setting that matches the tank, the 15 L setting displays about 9 % less than the actual power (code gives about 3.95 m/s, so about 110 W actual against 101 W shown), and the 19 L setting displays about 8 % more (about 3.72 m/s, so about 92 W actual against 100 W shown). At equal displayed watts, a lower setting therefore means higher actual work, which is why the 15 L code is higher. These numbers match the derived factors (c15/c17 about 0.90, c19/c17 about 1.07). Caveat: this assumes the 17 L setting matches the physical tank, and it relies on the 1.78 constant.

17 L check of the cube law against the monitor's own table (`rower/WaterRower.csv`, private, not in git):
- Pairs of pace (s per 500 m), m/s and monitor watts, 12 points from 2.94 to 5.38 m/s.
- Best fit through the origin, W = K v^3: K = 1.775. The mean of measured / (1.78 v^3) is 1.009 with sd 2.7 %.
- Free exponent on a log fit: 2.96 (3 expected).
- The table's own expectation column equals 1.78 v^3 within 0.6 W, and its average-watt column within 0.3 W.
- Caveat: these are the monitor's own watts, computed from its speed. The check confirms the monitor uses 1.78 v^3 at 17 L, which is the same law our demands use. It is not an independent physical measurement.

## 5e. Monitor-only points: the fill factor (3 Nov 2026)

Read straight from the monitor at a perfectly steady speed (no ticks, no decoder): speed in m/s and watts, then c = W / v^3.

| Fill | Monitor reading | c = W / v^3 | Factor against 1.78 (17 L) |
|---|---|---|---|
| 15 L | 4.0 m/s, 101 W (speed rounded, about ±0.02) | 1.578 | 0.887 (±1.5 %) |
| 17 L | the cube law, W = 1.78 v^3 | 1.78 | 1.00 |
| 19 L | 3.72 m/s, stable; 100 W, but could have been 101 W | 1.943 to 1.962 | 1.091 to 1.102 |
| 20 L | 3.65 m/s, 101 W, perfectly stable | 2.077 | 1.167 (±0.8 %) |

- A straight line through 17 L = 1.00 with a slope of 0.054 per litre (factor = 1 + 0.054 (L - 17)) fits all the points: 15 L 0.6 % below the line, 20 L 0.4 % above. The 19 L reading is 3.72 m/s at 100 W, but the 100 could have been 101: that gives c = 1.943 or 1.962, so the factor is 1.091 or 1.102 against 1.108 on the line. The first reading of 19 L looked like a 2 % outlier only because of that ±0.5 W. So the straight line is used.
- 18.5 L, 18 L, 13 L and beyond 20 L are not measured, only predicted by the line (13 L: 0.784, 18.5 L: 1.081).
- This tests the fill factor, not the 1.78 itself: the monitor computes watts from its own speed. 1.78 at 17 L stays.
- Code: `fill_factor(L) = 1 + 0.054 (L - 17)` in `rower/profiles.py` (`FILL_SLOPE_PER_L`), for any number of litres, fractions included. Effect on the demand for 100 W: 15 L asks for 3.99 m/s (was 3.83), 19 L for 3.71 m/s (was 3.75), 20 L for 3.63 m/s.
- Not changed: `CAL_LEVELS` in `rower/calib.py` still decides which fills the calibration page accepts.

Next: read the watts to the exact value at 19 L, 13 L, and a second power at one fill (60 W at 20 L) to see whether the factor holds across speed.

## 5f. Distance runs from rest to rest: the sensor is a pure tick counter (3 Nov 2026)

Four runs from rest to a full stop, recorded with the calibration page from Start to Abort. The ticks are the sum of every `P0x` value; the monitor's final distance was read after the flywheel stopped (whole metres).

| Run | Fill | Monitor | Ticks from the first stroke | Ticks per metre |
|---|---|---|---|---|
| `cal_2026-11-03_115610_19L` | 19 L | 218 m | 19,989 | 91.69 ± 0.21 |
| `cal_2026-11-03_115320_19L` | 19 L | 134 m | 12,296 (326 more before the first stroke) | 91.76 ± 0.34 |
| `cal_2026-11-03_120203_17L` | 17 L | 124 m | 11,410 | 92.02 ± 0.37 |
| `cal_2026-11-03_120028_17L` | 17 L | 67 m | 6,189 | 92.37 ± 0.69 |

- Weighted mean **91.8 ± 0.16 ticks per metre** (10.9 mm per tick, code 2.295 per m/s at 40 lines per second). 19 L: 91.71, 17 L: 92.10. No fill dependence beyond the rounding of the monitor's whole metres.
- The monitor counts distance **from the first stroke**, and counts the **whole coast** until the flywheel stops. Counting from time 0 gives 94.19 on the 134 m run (326 ticks before its first stroke). Counting from the second stroke or ignoring slow movement does not fit.
- The `P0x` lines are sent only when at least one tick was counted: the smallest value in all recordings is 1, and nothing arrives at rest. In a slow coast the lines are sparse, each with a 1.
- With the old per-line method the app reproduces the monitor on the 218 m run (217.8 m) only because two errors cancel: 2.2 % low while rowing, 15 % high in the coast (the 0.43 m/s floor of a mean of 1s). On the 134 m run it also counts the 3.7 m before the first stroke (138.8 m against 134 m). `CODE_PER_MS` was not changed.

## 5g. Decoder: speed and distance from the ticks (implemented 3 Nov, later)

Take speed and distance directly from the ticks, as the monitor does:
- speed = ticks in a time window (at least 1 s, because the timestamps come in bursts) / window / 91.8;
- distance = ticks since the first `SS` / 91.8;
- pace = 500 / speed; watts = c(L) × speed³ with c(L) = 1.78 × (1 + 0.054 (L - 17)).

The virtual landscape follows the measured distance and speed, and the display adds estimated watts and pace from the calibration. Implemented: `rower/profiles.py` (`TICKS_PER_M = 91.8`, `TICK_WINDOW_S = 1.0`). Speed is the ticks in the last second over 91.8, so absent lines count as zero. Distance is the ticks since the first stroke over 91.8, emitted as a `distance` sample. Power is updated once per completed stroke (SS to SS), from that stroke's ticks, as the monitor does. The tank fill is set on the row page (`Tank fill (L)`, 15.0 to 20.0 L). The engine takes distance from the `distance` sample, counted from the session start and shown in whole metres, as the monitor does. Speed is not used for distance. The status panel shows power and pace averaged over the last three completed strokes. Speed still feeds the dolphins.

## 5h. Power: from the averaged speed, updated once per stroke (3 Nov, decision)

- Power is calculated from the averaged speed signal: the mean of the 1 s speed over one stroke (from the previous SS to this SS), then `W = 1.78 * v^3 * fill factor` (`rower/profiles.py`, `Decoder`, the `_speeds` list reset at each SS).
- It is given only once per stroke (SS). The UI and the report see one power value per stroke, and nothing between strokes.
- Dropped: the velocity-interpolation scheme, per-second power, and power from a single stroke's tick sum. The sampled-line randomness was the reason for leaving them.
- The 1 s speed window starts at zero when the decoder starts, so the first stroke after a start is low. The test warms the window up first.
- The live display filters this per-stroke power (band 5 %, confirm 2 s, 15 s steady average). The report plots the per-stroke values, recomputed from the recorded codes and stroke markers.
- The same filter (band 5 %, confirm 2 s, 15 s steady average) is applied to the live speed (`speed_ms`). The pace (split) stays on the 3-stroke average.
- The same filter is applied to the live stroke rate (fed once per stroke, with the time between strokes as the step). The recording keeps the raw values.

## 5i. Filter choices (3 Nov)

- Filter: `rower/smoothing.py`, `AdaptiveSmoother`. While the value stays within a band of its average it is averaged with a 15 s exponential window (time-based, so irregular samples are fine). When a new level stays outside the band for a confirm time, the average jumps to the mean of that new level at once.
- Chosen settings: **band 5 %, confirm 2 s**, 15 s steady window.
- Live display (`rower/engine.py`, `RowState`):
  - power: filtered, fed once per completed stroke (the time between strokes is the step);
  - speed (`speed_ms`): filtered, fed per speed line (time step from the timestamps);
  - stroke rate: filtered, fed once per stroke;
  - pace (split): still the 3-stroke average, as chosen.
- Recording: raw values, unchanged (raw samples, raw 1 s speed, stroke markers).
- Postprocessing (`rower/report.py`): nine settings (band 5, 8, 12 %; confirm 1, 2, 3 s) for speed, power and stroke rate, to compare against raw. The dropdown defaults to band 5 %, confirm 2 s.
- Known limit: the spin-down lag (section 6) is not removed by this filter; it is a separate open item.

## 6. Open questions
- **Spin-down lag (kept as is for now).** Power comes from the flywheel speed, so when effort stops the speed falls slowly. The power readout stays high at the end of intervals even with no effort applied. Spin-up is not a problem. Future fix: a model of the flywheel deceleration, or a rule that lets the power fall with the stroke rather than with the speed.

1. **Averaging window.** The monitor appears to average over about 25 seconds. The 164 W ramp supports this. A clean step test would confirm it.
2. **Linear fill effect.** 15, 17, 19 and 20 L (monitor readings, 5e) lie on a straight line, 0.054 per litre, within the reading error (the 19 L watts were 100 or 101). 13 L and 18.5 L are still unmeasured.
3. **The 149 W run.** It was dropped. Re-run it with initial readings recorded, or accept the drop.
4. **Code scale.** The highest code seen is `P10` (16) at 300 W. Harder efforts may go higher.
5. **Unit behind the code.** Probably ring ticks per last period, see section 5b. Not needed for the current formula.

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
- `rower/calib.py`: fill-level calibration. Demands from the watt levels (100, 60, 150 W) through the cube law, rounded to 0.1 m/s. Steady-state detection on stroke-cycle means. Logs every rower sample from Start to the end.
- `static/row_cal.html`: calibration page at `/row-cal`. Watts big, pace small. Start, Retry, Skip, Redo previous, Abort.
- `tests/test_rower_cal.py`: tests for the detector and session (19 tests).
- Calibration output: `recordings/rows/cal_<time>_17L/` with `samples.csv` (full log), `trace.csv` (code per step) and `cal.json` (results).

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
3. Add a `rower_fill_litres` setting on the row page and apply `fill_factor(L)` (1 + 0.054 (L - 17)) when set. Still open; see 5g.
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
- Never ask the rower to type values while rowing. Detection is automatic, and the rower is assumed to hold the demanded speed.
