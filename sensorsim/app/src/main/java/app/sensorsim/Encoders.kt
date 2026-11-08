package app.sensorsim

import java.util.UUID
import kotlin.math.cbrt
import kotlin.math.floor
import kotlin.math.roundToInt

/** Bluetooth SIG UUIDs used by the rower simulator. */
object Uuids {
    private fun u(short: Int) = UUID.fromString(String.format("%08x-0000-1000-8000-00805f9b34fb", short))

    val CYCLING_POWER = u(0x1818)
    val CPS_MEASUREMENT = u(0x2A63)
    val CPS_FEATURE = u(0x2A65)
    val SENSOR_LOCATION = u(0x2A5D)

    val FTMS = u(0x1826)
    val FTMS_FEATURE = u(0x2ACC)
    val ROWER_DATA = u(0x2AD1)
    val FTMS_CONTROL_POINT = u(0x2AD9)

    val DEVICE_INFO = u(0x180A)
    val MANUFACTURER = u(0x2A29)
    val MODEL = u(0x2A24)
    val FIRMWARE = u(0x2A26)

    val CCCD = u(0x2902)
}

/**
 * Everything the Bluetooth rower reports at one moment. The first six values are measured or simulated; the averages
 * and the energy are filled in by [SessionStats].
 */
data class RowSnapshot(
    val strokeRateSpm: Double,
    val strokeCount: Long,
    val distanceM: Double,
    val speedMps: Double,
    val powerW: Int,
    val elapsedS: Int,
    val avgStrokeRateSpm: Double = 0.0,
    val avgPaceS: Int = 0,
    val avgPowerW: Int = 0,
    val energyKcal: Int = 0,
    val kcalPerHour: Int = 0,
    val kcalPerMinute: Int = 0,
    val crankTicks: Int = 0,   // time of the last stroke in 1/1024 s, for the Cycling Power crank data
)

/** Counts whole strokes at a variable rate, with the time of the last one in 1/1024 s (the BLE crank event time). */
class RevCounter {
    private var revs = 0.0
    private var lastEventMs = 0.0

    val count: Long get() = floor(revs).toLong()
    val eventTicks: Int get() = ((lastEventMs * 1.024).toLong() and 0xFFFF).toInt()

    /** Advance by dtMs at [perSecond] strokes per second; nowMs is the simulation clock AFTER the step. */
    fun advance(perSecond: Double, nowMs: Double, dtMs: Double) {
        if (perSecond <= 0.0 || dtMs <= 0.0) return
        val step = perSecond * dtMs / 1000.0
        val before = floor(revs)
        revs += step
        val after = floor(revs)
        if (after > before) {
            // time of the last whole-stroke crossing inside this step
            val frac = (after - (revs - step)) / step
            lastEventMs = nowMs - dtMs + frac * dtMs
        }
    }
}

/** What the sliders ask for. Speed is not an input: like on the real rower it follows from the power. */
data class Targets(val powerW: Int, val strokeRateSpm: Double)

/** Manual mode: integrates the slider values into strokes, distance and time, with speed from power (cube law). */
class ManualRower {
    private val strokes = RevCounter()
    private var distanceM = 0.0
    private var clockMs = 0.0
    private var last = Targets(0, 0.0)
    private var speed = 0.0

    fun advance(t: Targets, dtMs: Double, fillLitres: Double) {
        clockMs += dtMs
        last = t
        speed = if (t.powerW > 0) cbrt(t.powerW / (S4.WATT_CONST * S4.fillFactor(fillLitres))) else 0.0
        strokes.advance(t.strokeRateSpm / 60.0, clockMs, dtMs)
        distanceM += speed * dtMs / 1000.0
    }

    fun snapshot() = RowSnapshot(last.strokeRateSpm, strokes.count, distanceM, speed, last.powerW, (clockMs / 1000.0).toInt(),
        crankTicks = strokes.eventTicks)
}

/**
 * Session averages and a rough energy estimate, added to every snapshot. Energy is NOT measured: it assumes the
 * human body turns 25 % of its energy into rowing work (kcal = work / 4184 / 0.25). The S4 has no heart rate.
 */
class SessionStats(private val efficiency: Double = 0.25) {
    private var workJ = 0.0
    private var activeS = 0.0

    /** Call once per tick with the tick length in seconds. */
    fun add(s: RowSnapshot, dtS: Double) {
        if (s.strokeRateSpm > 0 || s.powerW > 0) {
            workJ += s.powerW * dtS
            activeS += dtS
        }
    }

    fun extend(s: RowSnapshot): RowSnapshot {
        val kcalPerHour = s.powerW * 3600.0 / 4184.0 / efficiency
        return s.copy(
            avgStrokeRateSpm = if (s.elapsedS > 0) s.strokeCount * 60.0 / s.elapsedS else 0.0,
            avgPaceS = if (s.elapsedS > 0 && s.distanceM >= 1.0) (500.0 * s.elapsedS / s.distanceM).roundToInt().coerceAtMost(0xFFFE) else 0,
            avgPowerW = if (activeS > 0) (workJ / activeS).roundToInt() else 0,
            energyKcal = (workJ / 4184.0 / efficiency).roundToInt(),
            kcalPerHour = kcalPerHour.roundToInt(),
            kcalPerMinute = (kcalPerHour / 60.0).roundToInt(),
        )
    }
}

/** Byte layouts of the characteristic values (little endian), after the Bluetooth SIG specifications. */
object Ble {
    private fun ByteArray.u8(i: Int, v: Int) { this[i] = v.toByte() }
    private fun ByteArray.u16(i: Int, v: Int) { this[i] = v.toByte(); this[i + 1] = (v shr 8).toByte() }
    private fun ByteArray.u24(i: Int, v: Long) { for (k in 0..2) this[i + k] = (v shr (8 * k)).toByte() }
    private fun ByteArray.u32(i: Int, v: Long) { for (k in 0..3) this[i + k] = (v shr (8 * k)).toByte() }

    /** Cycling Power Measurement 0x2A63: flags (crank revolution data present), power, crank revs (= strokes), crank event time. */
    fun cpsMeasurement(powerW: Int, crankRevs: Long, crankTicks: Int): ByteArray {
        val b = ByteArray(8)
        b.u16(0, 0x0020)
        b.u16(2, powerW.coerceIn(-32768, 32767))
        b.u16(4, (crankRevs and 0xFFFF).toInt())
        b.u16(6, crankTicks and 0xFFFF)
        return b
    }

    /** Cycling Power Feature 0x2A65: crank revolution data supported (bit 3). */
    fun cpsFeature(): ByteArray = ByteArray(4).also { it.u32(0, 0x08) }

    /** Sensor Location 0x2A5D. 13 = rear hub. */
    fun sensorLocation(): ByteArray = byteArrayOf(13)

    /** Rower Data pace value for "not available" (the rower is not moving): 0xFFFF. Zero would read as infinitely fast. */
    const val PACE_INVALID = 0xFFFF

    private fun paceField(paceS: Int): Int = if (paceS <= 0) PACE_INVALID else paceS

    /** Fitness Machine Feature 0x2ACC: distance (bit 2), pace (5), elapsed time (12). As in the working version. */
    fun ftmsFeature(): ByteArray = ByteArray(8).also { it.u32(0, 0x5024) }

    /** UTF-8 text value, as used by the Device Information characteristics. */
    fun text(s: String): ByteArray = s.toByteArray(Charsets.UTF_8)

    /** Seconds per 500 m for a speed in m/s; 0 when (almost) standing still. */
    fun paceS500(speedMps: Double): Int = if (speedMps < 0.1) 0 else (500.0 / speedMps).roundToInt().coerceAtMost(0xFFFE)

    /**
     * Rower Data 0x2AD1, the layout that worked in MyWhoosh: flags 0x082C = bits 2 (total distance), 3 (instantaneous
     * pace), 5 (instantaneous power), 11 (elapsed time); bit 0 is 0, so stroke rate and stroke count come first.
     * Fields: stroke rate u8 (0.5 spm), stroke count u16, distance u24 (m), pace u16 (s/500 m, 0 when still),
     * power s16 (W), elapsed time u16 (s): 14 bytes. The averages and energy fields are not sent (they were added
     * in a later version and MyWhoosh showed 2500 W with it).
     */
    fun rowerData(s: RowSnapshot): ByteArray {
        val b = ByteArray(14)
        b.u16(0, 0x082C)
        b.u8(2, (s.strokeRateSpm * 2).roundToInt().coerceIn(0, 255))
        b.u16(3, (s.strokeCount and 0xFFFF).toInt())
        b.u24(5, s.distanceM.toLong().coerceIn(0, 0xFFFFFF))
        b.u16(8, paceS500(s.speedMps))
        b.u16(10, s.powerW.coerceIn(-32768, 32767))
        b.u16(12, s.elapsedS.coerceIn(0, 0xFFFF))
        return b
    }

    /**
     * Fitness Machine control point reply (indication): 0x80, the request op code, a result code. Request control (0x00),
     * reset (0x01), start/resume (0x07) and stop/pause (0x08) succeed (0x01); everything else, such as target settings or
     * bike simulation, is "op code not supported" (0x02).
     */
    fun controlPointReply(op: Int): ByteArray {
        val result = if (op == 0x00 || op == 0x01 || op == 0x07 || op == 0x08) 0x01 else 0x02
        return byteArrayOf(0x80.toByte(), op.toByte(), result.toByte())
    }
}
