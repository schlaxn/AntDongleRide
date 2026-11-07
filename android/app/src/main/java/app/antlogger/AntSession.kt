package app.antlogger

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Spike session: opens wildcard receive channels, logs every received frame raw to a CSV
 * (ts_ms,hex) and reports a human-readable line per frame plus per-page counters.
 */
class AntSession(
    private val usb: UsbAnt,
    private val rawLog: File,
    private val onLine: (String) -> Unit,
) {
    @Volatile private var running = false
    private var readerThread: Thread? = null
    private var setupThread: Thread? = null
    private val parser = FrameParser()
    private val channelType = java.util.concurrent.ConcurrentHashMap<Int, Int>()

    /** "devtype:devnum:page" -> count */
    val pageCounts = java.util.concurrent.ConcurrentHashMap<String, Int>()

    /** Called from the setup thread only. The reader thread keeps draining the stick meanwhile. */
    private fun send(msgId: Int, data: ByteArray = ByteArray(0)) {
        if (!usb.write(Ant.encode(msgId, data))) onLine("WRITE FAILED msg 0x%02X".format(msgId))
        Thread.sleep(10)
    }

    @Volatile private var needReconfig = false
    @Volatile var resets = 0
        private set
    @Volatile var lastDataMs = 0L
        private set
    @Volatile private var configured = false

    private fun configureChannels() {
        // two power channels: the pedals may present as two devices
        listen(0, 120)
        listen(1, 11)
        listen(2, 11)
        listen(3, 121)
        configured = true
    }

    private fun listen(channel: Int, devType: Int) {
        val period = Ant.DEVICE_TYPES.getValue(devType).second
        send(Ant.SET_NET_KEY, byteArrayOf(0) + Ant.NETWORK_KEY)
        send(Ant.ASSIGN_CH, byteArrayOf(channel.toByte(), 0x00, 0x00)) // 0x00 = receive channel
        val id = ByteBuffer.allocate(5).order(ByteOrder.LITTLE_ENDIAN)
            .put(channel.toByte()).putShort(0).put(devType.toByte()).put(0).array()
        send(Ant.SET_CH_ID, id)
        val per = ByteBuffer.allocate(3).order(ByteOrder.LITTLE_ENDIAN)
            .put(channel.toByte()).putShort(period.toShort()).array()
        send(Ant.SET_PERIOD, per)
        send(Ant.SET_RF_FREQ, byteArrayOf(channel.toByte(), Ant.RF_FREQ_ANTPLUS.toByte()))
        // never give up searching: sensors (pedals) may start broadcasting minutes later
        send(Ant.SET_HP_SEARCH_TIMEOUT, byteArrayOf(channel.toByte(), 0x00)) // 0 = no high-priority phase
        send(Ant.SET_LP_SEARCH_TIMEOUT, byteArrayOf(channel.toByte(), 0xFF.toByte())) // 255 = infinite
        send(Ant.ENABLE_EXT, byteArrayOf(0x00, 0x01)) // extended data incl. sensor device number
        send(Ant.OPEN_CH, byteArrayOf(channel.toByte()))
        channelType[channel] = devType
        onLine("channel $channel listening for ${Ant.DEVICE_TYPES.getValue(devType).first}")
    }

    fun start() {
        running = true
        // Reader: only ever reads and logs, so the stick's output queue can never back up.
        readerThread = Thread({
            try {
                rawLog.bufferedWriter().use { log ->
                    while (running) handle(log, usb.read(100))
                    log.flush()
                }
            } catch (e: Exception) {
                onLine("READER ERROR: ${e.message}")
            }
            running = false
        }, "ant-reader").also { it.start() }
        // Setup: all writes (reset, channel configuration, watchdog re-configuration).
        setupThread = Thread({
            try {
                // the stick may still hold open channels from a killed earlier session: reset it
                send(Ant.RESET, byteArrayOf(0))
                Thread.sleep(800)
                configureChannels()
                while (running) {
                    if (needReconfig) {
                        // the stick restarted by itself: its channels are gone, set them up again
                        needReconfig = false
                        resets++
                        onLine("STICK RESET #$resets detected, re-configuring channels")
                        channelType.clear()
                        Thread.sleep(300)
                        configureChannels()
                    }
                    Thread.sleep(100)
                }
            } catch (e: InterruptedException) {
                // stop() interrupts us
            } catch (e: Exception) {
                onLine("SETUP ERROR: ${e.message}")
            }
        }, "ant-setup").also { it.start() }
    }

    private var lastFlush = 0L

    private fun handle(log: java.io.Writer, chunk: ByteArray) {
        val now = System.currentTimeMillis()
        if (chunk.isNotEmpty()) {
            for (f in parser.feed(chunk)) {
                log.write("$now,${f.raw.hex().replace(" ", "")}\n")
                describe(f)?.let { onLine(it) }
            }
        }
        if (now - lastFlush > 1000) { // keep the file current so a crash or pull loses at most 1 s
            log.flush()
            lastFlush = now
        }
    }

    private fun describe(f: Frame): String? {
        val d = f.data
        when (f.msgId) {
            Ant.STARTUP -> {
                if (configured) needReconfig = true // a startup message after setup = unexpected reset
                return "stick startup flags 0x%02X".format(d.firstOrNull()?.toInt() ?: 0)
            }
            Ant.CHANNEL_EVENT -> {
                if (d.size < 3) return null
                // a channel-event RX_FAIL (0x02) is routine noise; skip it
                if (d[1].toInt() == 0x01 && d[2].toInt() == 0x02) return null
                return "ch${d[0]} ev/resp 0x%02X code 0x%02X".format(d[1], d[2])
            }
            Ant.BROADCAST, Ant.ACK_DATA, Ant.BURST -> {
                if (d.size < 9) return null
                var devType = channelType[d[0].toInt()] ?: 0
                var devNum = 0
                if (d.size >= 14 && (d[9].toInt() and 0x80) != 0) {
                    devNum = (d[10].toInt() and 0xFF) or ((d[11].toInt() and 0xFF) shl 8)
                    devType = d[12].toInt() and 0xFF
                }
                lastDataMs = System.currentTimeMillis()
                val page = d[1].toInt() and 0xFF
                val key = "$devType:$devNum:0x%02X".format(page)
                pageCounts.merge(key, 1, Int::plus)
                return "ch${d[0]} type=$devType dev=$devNum  ${d.copyOfRange(1, 9).hex()}"
            }
        }
        return null
    }

    fun stop() {
        running = false
        setupThread?.interrupt()
        setupThread?.join(2000)
        readerThread?.join(2000)
        try {
            // leave the stick clean so the next session does not inherit open channels
            usb.write(Ant.encode(Ant.RESET, byteArrayOf(0)))
        } catch (_: Exception) {
        }
        usb.close()
    }
}
