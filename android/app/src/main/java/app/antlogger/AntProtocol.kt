package app.antlogger

/** Port of ant/protocol.py: ANT serial framing. SYNC(0xA4) | LEN | MSG_ID | DATA[LEN] | XOR checksum. */
object Ant {
    const val SYNC = 0xA4
    const val RF_FREQ_ANTPLUS = 57 // 2457 MHz

    val NETWORK_KEY = byteArrayOf(
        0xB9.toByte(), 0xA5.toByte(), 0x21, 0xFB.toByte(),
        0xBD.toByte(), 0x72, 0xC3.toByte(), 0x45,
    )

    const val RESET = 0x4A
    const val SET_NET_KEY = 0x46
    const val ASSIGN_CH = 0x42
    const val SET_CH_ID = 0x51
    const val SET_PERIOD = 0x43
    const val SET_RF_FREQ = 0x45
    const val SET_HP_SEARCH_TIMEOUT = 0x44
    const val SET_LP_SEARCH_TIMEOUT = 0x63
    const val OPEN_CH = 0x4B
    const val CLOSE_CH = 0x4C
    const val ENABLE_EXT = 0x66
    const val CHANNEL_EVENT = 0x40
    const val BROADCAST = 0x4E
    const val ACK_DATA = 0x4F
    const val BURST = 0x50
    const val STARTUP = 0x6F

    // device type -> (name, channel period in 1/32768 s)
    val DEVICE_TYPES = mapOf(
        120 to ("Heart rate" to 8070),
        11 to ("Bike power" to 8182),
        121 to ("Bike speed & cadence" to 8086),
        122 to ("Bike cadence" to 8102),
        123 to ("Bike speed" to 8118),
    )

    /**
     * HARD CONSTRAINT: this app only listens. The only messages it may send are the ones that configure
     * the stick itself (reset, network key, receive channels, search timeouts, extended data, open/close).
     * Everything that could put data on the air toward a sensor (broadcast, acknowledged, burst, advanced
     * burst, ...) is not on this list and is refused.
     */
    val STICK_CONFIG_MSGS = setOf(
        RESET, SET_NET_KEY, ASSIGN_CH, SET_CH_ID, SET_PERIOD, SET_RF_FREQ,
        SET_HP_SEARCH_TIMEOUT, SET_LP_SEARCH_TIMEOUT, ENABLE_EXT, OPEN_CH, CLOSE_CH,
    )

    /** Throws unless [raw] is a well-formed frame carrying one of [STICK_CONFIG_MSGS]. */
    fun requireReadOnly(raw: ByteArray) {
        val id = if (raw.size >= 4) raw[2].toInt() and 0xFF else -1
        check(raw.size >= 4 && (raw[0].toInt() and 0xFF) == SYNC && id in STICK_CONFIG_MSGS) {
            "read-only app: refusing to send ANT message 0x%02X".format(id and 0xFF)
        }
    }

    fun checksum(body: ByteArray, len: Int = body.size): Int {
        var c = 0
        for (i in 0 until len) c = c xor (body[i].toInt() and 0xFF)
        return c
    }

    fun encode(msgId: Int, data: ByteArray = ByteArray(0)): ByteArray {
        val out = ByteArray(data.size + 4)
        out[0] = SYNC.toByte()
        out[1] = data.size.toByte()
        out[2] = msgId.toByte()
        data.copyInto(out, 3)
        out[out.size - 1] = checksum(out, out.size - 1).toByte()
        return out
    }
}

class Frame(val msgId: Int, val data: ByteArray, val raw: ByteArray)

/** Incremental parser: feed arbitrary chunks, get complete frames back. */
class FrameParser {
    private var buf = ByteArray(0)
    var badChecksums = 0
        private set

    fun feed(chunk: ByteArray, len: Int = chunk.size): List<Frame> {
        buf += chunk.copyOf(len)
        val out = ArrayList<Frame>()
        while (true) {
            val i = buf.indexOfFirst { (it.toInt() and 0xFF) == Ant.SYNC }
            if (i < 0) {
                buf = ByteArray(0)
                break
            }
            if (i > 0) buf = buf.copyOfRange(i, buf.size)
            if (buf.size < 2) break
            val total = (buf[1].toInt() and 0xFF) + 4
            if (buf.size < total) break
            val raw = buf.copyOfRange(0, total)
            if (Ant.checksum(raw, total - 1) != (raw[total - 1].toInt() and 0xFF)) {
                badChecksums++
                buf = buf.copyOfRange(1, buf.size) // resync: drop the sync byte, look again
                continue
            }
            buf = buf.copyOfRange(total, buf.size)
            out.add(Frame(raw[2].toInt() and 0xFF, raw.copyOfRange(3, total - 1), raw))
        }
        return out
    }
}

fun ByteArray.hex(): String = joinToString(" ") { "%02X".format(it.toInt() and 0xFF) }
