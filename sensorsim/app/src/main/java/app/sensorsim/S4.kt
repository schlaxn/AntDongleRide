package app.sensorsim

import kotlin.math.max
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * WaterRower S4 monitor: line parsing and decoding. Port of rower/protocol.py and rower/profiles.py; the
 * interface is described in ROWER_INTERFACE.md (repo root). 115200 baud 8N1, ASCII lines ending in \r\n.
 */
object S4 {
    /** The ONLY thing the app ever sends to the monitor: the handshake after which it starts streaming. */
    val HANDSHAKE: ByteArray = "USB\r\n".toByteArray(Charsets.US_ASCII)

    fun requireHandshakeOnly(data: ByteArray) {
        require(data.contentEquals(HANDSHAKE)) { "only the USB handshake may be sent to the rower monitor" }
    }

    const val TICKS_PER_M = 91.8       // ring-segment ticks per metre (four rest-to-rest runs)
    const val SPEED_WINDOW_S = 15.0    // speed = ticks counted over the last 15 s (less while the session is younger)
    const val WATT_CONST = 1.78        // W per (m/s)^3 at 17 L
    const val FILL_SLOPE_PER_L = 0.054 // displayed-power factor per litre of fill setting, relative to 17 L
    const val STROKE_TIMEOUT_S = 5.0   // no stroke for this long: stroke rate and power read zero

    fun fillFactor(litres: Double): Double = max(0.5, 1.0 + FILL_SLOPE_PER_L * (litres - 17.0))
}

sealed interface S4Line {
    data class Pulse(val ticks: Int) : S4Line   // "P0A" = 10 ticks in the last 25 ms
    data object StrokeStart : S4Line            // "SS"
    data object StrokeEnd : S4Line              // "SE"
    data object Ping : S4Line                   // "PING", once a second
    data object Other : S4Line                  // anything else ("AKR" at startup)
}

private val PULSE_RE = Regex("^P([0-9A-Fa-f]{2})$")

fun parseS4Line(line: String): S4Line {
    val l = line.trim()
    return when (l) {
        "SS" -> S4Line.StrokeStart
        "SE" -> S4Line.StrokeEnd
        "PING" -> S4Line.Ping
        else -> PULSE_RE.matchEntire(l)?.let { S4Line.Pulse(it.groupValues[1].toInt(16)) } ?: S4Line.Other
    }
}

/** Splits a byte stream into lines (terminated by \n, a preceding \r is dropped). */
class LineSplitter {
    private val buf = StringBuilder()

    fun feed(bytes: ByteArray, n: Int): List<String> {
        buf.append(String(bytes, 0, n, Charsets.ISO_8859_1))
        val out = ArrayList<String>()
        while (true) {
            val i = buf.indexOf("\n")
            if (i < 0) return out
            out.add(buf.substring(0, i).trimEnd('\r'))
            buf.delete(0, i + 1)
        }
    }
}

/**
 * Stateful decoder. Speed is the ticks counted over the last SPEED_WINDOW_S seconds (fewer while the session is younger)
 * over TICKS_PER_M, so it is an average of at least 15 s and does not jump with each burst of lines. Distance is the ticks
 * since the first stroke over TICKS_PER_M, stroke rate from SS to SS, and power once per completed stroke from the averaged
 * speed signal with the cube law and the fill setting. Timestamps are seconds on any monotonic clock.
 */
class S4Decoder(@Volatile var fillLitres: Double = 17.0) {
    private val recent = ArrayDeque<Pair<Double, Int>>()   // (ts, ticks) inside the speed window
    private var firstPulse: Double? = null
    private var cycleTicks = 0
    private var speedSum = 0.0
    private var speedN = 0
    private var lastSs: Double? = null
    private var firstSs: Double? = null
    private var lastSeen = 0.0
    private var distanceTicks = 0L
    private var strokes = 0L
    private var strokeRate = 0.0
    private var powerW = 0.0
    private var speedFilter = AdaptiveSmoother()   // the displayed speed: 15 s steady average, fast path for real changes
    private var lastSnapshot: Double? = null

    @Synchronized
    fun reset() {
        speedFilter = AdaptiveSmoother(); lastSnapshot = null
        recent.clear(); firstPulse = null; cycleTicks = 0; speedSum = 0.0; speedN = 0
        lastSs = null; firstSs = null; lastSeen = 0.0; distanceTicks = 0; strokes = 0; strokeRate = 0.0; powerW = 0.0
    }

    /** Speed in m/s at time [now], averaged over the window; the divisor is the time covered so far (at least 1 s). */
    private fun speedAt(now: Double): Double {
        val first = firstPulse ?: return 0.0
        while (recent.isNotEmpty() && now - recent.first().first >= S4.SPEED_WINDOW_S) recent.removeFirst()
        val span = minOf(S4.SPEED_WINDOW_S, maxOf(1.0, now - first))
        return recent.sumOf { it.second } / span / S4.TICKS_PER_M
    }

    @Synchronized
    fun onLine(line: String, ts: Double) {
        when (val p = parseS4Line(line)) {
            is S4Line.Pulse -> {
                if (firstPulse == null) firstPulse = ts
                recent.addLast(ts to p.ticks)
                val v = speedAt(ts)
                speedSum += v; speedN++
                cycleTicks += p.ticks
                if (firstSs != null) distanceTicks += p.ticks
                lastSeen = ts
            }
            is S4Line.StrokeStart -> {
                strokes++
                val prev = lastSs
                if (prev != null) {
                    val dt = ts - prev
                    if (dt > 0 && dt < 10) {   // ignore absurd gaps (session start, a long pause)
                        strokeRate = 60.0 / dt
                        if (speedN > 0) {
                            val vAvg = speedSum / speedN
                            powerW = S4.WATT_CONST * vAvg.pow(3) * S4.fillFactor(fillLitres)
                        }
                    }
                }
                cycleTicks = 0; speedSum = 0.0; speedN = 0
                if (firstSs == null) { firstSs = ts; distanceTicks = 0 }
                lastSs = ts
                lastSeen = ts
            }
            is S4Line.StrokeEnd -> lastSeen = ts
            S4Line.Ping, S4Line.Other -> {}
        }
    }

    /** The state at time [now]: speed decays to zero when no pulses arrive, stroke rate and power after a timeout. */
    @Synchronized
    fun snapshot(now: Double): RowSnapshot {
        val dt = lastSnapshot?.let { now - it } ?: 0.0
        lastSnapshot = now
        val speed = speedFilter.update(speedAt(now), dt)
        val stale = lastSs.let { it == null || now - it > S4.STROKE_TIMEOUT_S }
        val first = firstSs
        val elapsed = if (first == null) 0 else max(0.0, lastSeen - first).toInt()
        return RowSnapshot(
            strokeRateSpm = if (stale) 0.0 else strokeRate,
            strokeCount = strokes,
            distanceM = distanceTicks / S4.TICKS_PER_M,
            speedMps = speed,
            powerW = if (stale) 0 else powerW.roundToInt(),
            elapsedS = elapsed,
            crankTicks = (((lastSs ?: 0.0) * 1024).toLong() and 0xFFFF).toInt(),   // time of the last stroke, 1/1024 s
        )
    }
}
