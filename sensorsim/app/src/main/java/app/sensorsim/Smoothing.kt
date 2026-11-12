package app.sensorsim

import kotlin.math.abs
import kotlin.math.min

/**
 * Adaptive smoothing for a value that changes over time, a port of rower/smoothing.py from the laptop app.
 *
 * While the value is steady it is averaged with an exponential window of [tauS] seconds. When a new level is sustained
 * (the value stays outside the [band] around the average for [confirmS] seconds), the average jumps to that level at
 * once, so a deliberate change shows as soon as it is confirmed, without the 15 s lag.
 */
class AdaptiveSmoother(
    private val tauS: Double = 15.0,
    private val band: Double = 0.05,
    private val confirmS: Double = 2.0,
    private val dtS: Double = 1.0,
) {
    /** The current average, or null before the first value. */
    var avg: Double? = null
        private set
    private val pending = ArrayList<Double>()
    private var pendingS = 0.0

    /** Feeds [y], which came [dt] seconds after the previous value (the sample spacing when [dt] is not positive). */
    fun update(y: Double, dt: Double = dtS): Double {
        val step = if (dt <= 0.0) dtS else dt
        val a = avg
        if (a == null || a <= 0.0) {
            avg = y
            return y
        }
        val dev = abs(y - a) / a
        if (dev > band) {
            pending += y
            pendingS += step
            if (pendingS >= confirmS) {
                // the change is confirmed: follow it with the recent values of the change
                avg = pending.average()
                pending.clear()
                pendingS = 0.0
            }
            return avg!!
        }
        // steady: the change was a blip; drop it and average normally
        pending.clear()
        pendingS = 0.0
        val alpha = min(1.0, step / tauS)
        avg = a + (y - a) * alpha
        return avg!!
    }
}
