package app.antlogger

/** One decoded value. Metric names are listed in [Metrics]. */
data class Sample(
    val tsMs: Long,
    val devType: Int,
    val devNum: Int,
    val metric: String,
    val value: Double,
)

object Metrics {
    const val HEART_RATE = "heart_rate"
    const val POWER = "power"
    const val CADENCE = "cadence"
    const val BALANCE_RIGHT = "pedal_balance_right" // % of power from the right pedal
    const val TE_LEFT = "torque_eff_left" // %
    const val TE_RIGHT = "torque_eff_right"
    const val PS_LEFT = "pedal_smooth_left" // %
    const val PS_RIGHT = "pedal_smooth_right"
    const val PS_COMBINED = "pedal_smooth_combined"
    const val PCO_LEFT = "pco_left" // mm, + = outward
    const val PCO_RIGHT = "pco_right"
    const val RIDER_POSITION = "rider_position" // 0 seated, 1 to seated, 2 standing, 3 to standing
    const val BARYCENTER = "torque_barycenter_deg" // degrees
    const val BATTERY_V = "battery_voltage"
    const val BATTERY_STATUS = "battery_status" // 1 new, 2 good, 3 ok, 4 low, 5 critical
    const val MANUFACTURER = "manufacturer_id"
    const val MODEL = "model_number"
    const val HW_REV = "hardware_revision"
    const val SW_VER = "software_version"
    const val SERIAL = "serial_number"

    /** Force-angle metrics per side: fa_<left|right>_<field>; angles in degrees, torque in Nm. */
    fun fa(side: String, field: String) = "fa_${side}_$field"
    val FA_FIELDS = listOf("start_deg", "end_deg", "peak_start_deg", "peak_end_deg", "torque_nm")
}

/** A broadcast data message with the sensor identity taken from the extended-data flag. */
class Broadcast(val channel: Int, val devType: Int, val devNum: Int, val payload: ByteArray)

/**
 * Page decoding after ANT+ Bicycle Power profile Rev 5.1 (D00001086) and the ANT+ common pages.
 * The pages 0xE0/0xE1/0xE2/0x14 are the cycling dynamics pages (chapter 17).
 */
object Decoder {
    const val DEV_HR = 120
    const val DEV_POWER = 11

    val DEDUPED_POWER_PAGES = setOf(0x13, 0xE0, 0xE1, 0xE2, 0x14)

    /** Splits a BROADCAST/ACK/BURST frame into channel, sensor identity and the 8-byte payload. */
    fun broadcast(f: Frame, channelDevType: (Int) -> Int = { 0 }): Broadcast? {
        if (f.msgId != Ant.BROADCAST && f.msgId != Ant.ACK_DATA && f.msgId != Ant.BURST) return null
        val d = f.data
        if (d.size < 9) return null
        val channel = d[0].toInt() and 0xFF
        var devType = channelDevType(channel)
        var devNum = 0
        if (d.size >= 14 && (d[9].toInt() and 0x80) != 0) {
            devNum = (d[10].toInt() and 0xFF) or ((d[11].toInt() and 0xFF) shl 8)
            devType = d[12].toInt() and 0xFF
        }
        return Broadcast(channel, devType, devNum, d.copyOfRange(1, 9))
    }

    private fun brad(b: Int) = b * 360.0 / 256.0

    fun decode(tsMs: Long, devType: Int, devNum: Int, p: ByteArray): List<Sample> {
        val out = ArrayList<Sample>(8)
        fun u8(i: Int) = p[i].toInt() and 0xFF
        fun s8(i: Int) = p[i].toInt()
        fun add(m: String, v: Double) { out += Sample(tsMs, devType, devNum, m, v) }
        val page = u8(0)

        // ANT+ common pages, valid for every device type
        when (page) {
            0x50 -> if (devType != DEV_HR) { // HR pages use these numbers for their own data
                add(Metrics.HW_REV, u8(3).toDouble())
                add(Metrics.MANUFACTURER, (u8(4) or (u8(5) shl 8)).toDouble())
                add(Metrics.MODEL, (u8(6) or (u8(7) shl 8)).toDouble())
                return out
            }
            0x51 -> if (devType != DEV_HR) {
                if (u8(3) != 0xFF) add(Metrics.SW_VER, u8(3).toDouble())
                val serial = u8(4).toLong() or (u8(5).toLong() shl 8) or (u8(6).toLong() shl 16) or (u8(7).toLong() shl 24)
                if (serial != 0xFFFFFFFFL) add(Metrics.SERIAL, serial.toDouble())
                return out
            }
            0x52 -> if (devType != DEV_HR) {
                val coarse = u8(7) and 0x0F
                val status = (u8(7) shr 4) and 0x07
                if (coarse != 0x0F) add(Metrics.BATTERY_V, coarse + u8(6) / 256.0)
                if (status in 1..5) add(Metrics.BATTERY_STATUS, status.toDouble())
                return out
            }
        }

        when (devType) {
            DEV_HR -> {
                // every heart-rate page carries the computed heart rate in byte 7; 0 = invalid
                val hr = u8(7)
                if (hr in 1..254) add(Metrics.HEART_RATE, hr.toDouble())
            }
            DEV_POWER -> when (page) {
                0x10 -> {
                    // byte 2 pedal power (bit 7 = right pedal share, bits 0-6 percent), 3 cadence, 6-7 power
                    val pw = u8(6) or (u8(7) shl 8)
                    if (pw != 0xFFFF) add(Metrics.POWER, pw.toDouble())
                    if (u8(3) != 0xFF) add(Metrics.CADENCE, u8(3).toDouble())
                    if (u8(2) != 0xFF && (u8(2) and 0x80) != 0) add(Metrics.BALANCE_RIGHT, (u8(2) and 0x7F).toDouble())
                }
                0x13 -> {
                    // 1/2 % resolution; 0xFF invalid; byte 5 == 0xFE: byte 4 is a combined smoothness
                    if (u8(2) != 0xFF) add(Metrics.TE_LEFT, u8(2) / 2.0)
                    if (u8(3) != 0xFF) add(Metrics.TE_RIGHT, u8(3) / 2.0)
                    if (u8(5) == 0xFE) {
                        if (u8(4) != 0xFF) add(Metrics.PS_COMBINED, u8(4) / 2.0)
                    } else {
                        if (u8(4) != 0xFF) add(Metrics.PS_LEFT, u8(4) / 2.0)
                        if (u8(5) != 0xFF) add(Metrics.PS_RIGHT, u8(5) / 2.0)
                    }
                }
                0xE0, 0xE1 -> {
                    val side = if (page == 0xE0) "right" else "left"
                    // brads (1/256 turn), clockwise from the top; start+end both 0xC0 = invalid
                    if (!(u8(2) == 0xC0 && u8(3) == 0xC0)) {
                        add(Metrics.fa(side, "start_deg"), brad(u8(2)))
                        add(Metrics.fa(side, "end_deg"), brad(u8(3)))
                    }
                    if (!(u8(4) == 0xC0 && u8(5) == 0xC0)) {
                        add(Metrics.fa(side, "peak_start_deg"), brad(u8(4)))
                        add(Metrics.fa(side, "peak_end_deg"), brad(u8(5)))
                    }
                    add(Metrics.fa(side, "torque_nm"), (u8(6) or (u8(7) shl 8)) / 32.0)
                }
                0xE2 -> {
                    add(Metrics.RIDER_POSITION, ((u8(2) shr 6) and 0x03).toDouble())
                    if (s8(4) != -128) add(Metrics.PCO_RIGHT, s8(4).toDouble())
                    if (s8(5) != -128) add(Metrics.PCO_LEFT, s8(5).toDouble())
                    if (u8(3) != 0xFF) add(Metrics.CADENCE, u8(3).toDouble())
                }
                0x14 -> if (u8(1) != 0xFF) add(Metrics.BARYCENTER, 30.0 + u8(1) * 0.5)
            }
        }
        return out
    }
}

/** Reads the raw capture CSV (`ts_ms,hex[,T]` per ANT frame; T = sent by us) into samples. */
object CaptureReader {
    fun read(lines: Sequence<String>): List<Sample> {
        val out = ArrayList<Sample>()
        val last = HashMap<String, ByteArray>()
        for (line in lines) {
            val parts = line.split(',')
            if (parts.size < 2 || parts.size > 2) continue // 3 parts = frame we sent (T)
            val ts = parts[0].toLongOrNull() ?: continue
            val hex = parts[1].trim()
            if (hex.length < 10 || hex.length % 2 != 0) continue // truncated tail line
            val raw = try {
                ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
            } catch (_: NumberFormatException) {
                continue
            }
            for (f in FrameParser().feed(raw)) {
                val b = Decoder.broadcast(f) ?: continue
                val page = b.payload[0].toInt() and 0xFF
                if (b.devType == Decoder.DEV_POWER && page in Decoder.DEDUPED_POWER_PAGES) {
                    val key = "${b.devNum}:$page"
                    if (last[key]?.contentEquals(b.payload) == true) continue
                    last[key] = b.payload
                }
                out += Decoder.decode(ts, b.devType, b.devNum, b.payload)
            }
        }
        return out
    }
}
