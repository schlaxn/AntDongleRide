package app.antlogger

/** One decoded value. [metric] names follow ant/profiles.py: heart_rate, power, cadence, pedal_balance_right. */
data class Sample(
    val tsMs: Long,
    val devType: Int,
    val devNum: Int,
    val metric: String,
    val value: Double,
)

/** A broadcast data message with the sensor identity taken from the extended-data flag. */
class Broadcast(val channel: Int, val devType: Int, val devNum: Int, val payload: ByteArray)

object Decoder {
    const val DEV_HR = 120
    const val DEV_POWER = 11

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

    fun decode(tsMs: Long, devType: Int, devNum: Int, p: ByteArray): List<Sample> {
        val out = ArrayList<Sample>(3)
        fun u8(i: Int) = p[i].toInt() and 0xFF
        when (devType) {
            DEV_HR -> {
                // every heart-rate page carries the computed heart rate in byte 7; 0 = invalid
                val hr = u8(7)
                if (hr in 1..254) out += Sample(tsMs, devType, devNum, "heart_rate", hr.toDouble())
            }
            DEV_POWER -> {
                // page 0x10 (standard power-only): byte 2 pedal balance, byte 3 cadence, bytes 6-7 power
                if (u8(0) == 0x10) {
                    out += Sample(tsMs, devType, devNum, "power", (u8(6) or (u8(7) shl 8)).toDouble())
                    if (u8(3) != 0xFF) out += Sample(tsMs, devType, devNum, "cadence", u8(3).toDouble())
                    if (u8(2) != 0xFF && (u8(2) and 0x80) != 0) {
                        // bit 7 set = the percentage belongs to the right pedal; low 7 bits = percent
                        out += Sample(tsMs, devType, devNum, "pedal_balance_right", (u8(2) and 0x7F).toDouble())
                    }
                }
            }
        }
        return out
    }
}

/** Reads the raw capture CSV (`ts_ms,hex` per ANT frame) written by [AntSession] into samples. */
object CaptureReader {
    fun read(lines: Sequence<String>): List<Sample> {
        val out = ArrayList<Sample>()
        for (line in lines) {
            val comma = line.indexOf(',')
            if (comma <= 0) continue
            val ts = line.substring(0, comma).toLongOrNull() ?: continue
            val hex = line.substring(comma + 1).trim()
            if (hex.length < 10 || hex.length % 2 != 0) continue // truncated tail line
            val raw = try {
                ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
            } catch (_: NumberFormatException) {
                continue
            }
            for (f in FrameParser().feed(raw)) {
                val b = Decoder.broadcast(f) ?: continue
                out += Decoder.decode(ts, b.devType, b.devNum, b.payload)
            }
        }
        return out
    }
}
