package app.antlogger

import java.io.File
import java.io.Writer
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentHashMap

/**
 * Owns the stick. Two threads: the reader only reads/decodes/logs (so the stick's output queue never
 * backs up), the setup thread does every write (reset, channels, cycling-dynamics enabling).
 *
 * Logged per session: raw frames (`ts_ms,hex` and `ts_ms,hex,T` for frames we send) and every decoded
 * sample (`ts_ms,devtype,devnum,metric,value`). The raw log is the source of truth.
 */
class AntSession(
    private val usb: UsbAnt,
    private val rawLog: File,
    private val samplesLog: File,
    private val onLine: (String) -> Unit,
) {
    @Volatile private var running = false
    private var readerThread: Thread? = null
    private var setupThread: Thread? = null
    private val parser = FrameParser()
    private val channelType = ConcurrentHashMap<Int, Int>()

    /** "devtype:devnum:page" -> count */
    val pageCounts = ConcurrentHashMap<String, Int>()

    /** metric -> (latest value, timestamp ms). Read by the live display. */
    val live = ConcurrentHashMap<String, Pair<Double, Long>>()

    /** devNum of power sensors seen -> channel they arrive on. */
    private val powerDevices = ConcurrentHashMap<Int, Int>()
    /** devNum -> (capabilities mask, capabilities value) from Advanced Capabilities 2 (page 0x02, 0xFE). */
    private val caps = ConcurrentHashMap<Int, IntArray>()
    /** channel -> last transfer result code (5 = completed, 6 = failed). */
    private val txResult = ConcurrentHashMap<Int, Int>()
    private val closedChannels = ConcurrentHashMap.newKeySet<Int>()
    private val handled = ConcurrentHashMap.newKeySet<Int>()
    private val lastPayload = ConcurrentHashMap<String, ByteArray>()

    /** Ask power sensors to transmit cycling dynamics (8 Hz mode). Standard ANT+ procedure. */
    @Volatile var enableDynamics = true
    @Volatile var dynStatus = "dynamics: waiting for a power sensor"
        private set
    @Volatile private var needReconfig = false
    @Volatile var resets = 0
        private set
    @Volatile var lastDataMs = 0L
        private set
    @Volatile private var configured = false

    private val logLock = Any()
    private var rawWriter: Writer? = null
    private var samplesWriter: Writer? = null
    private val tracker = SensorTracker()

    // ---------- writing (setup thread only) ----------
    private fun logRaw(raw: ByteArray, tx: Boolean) {
        synchronized(logLock) {
            rawWriter?.write("${System.currentTimeMillis()},${raw.hex().replace(" ", "")}${if (tx) ",T" else ""}\n")
        }
    }

    private fun send(msgId: Int, data: ByteArray = ByteArray(0)) {
        val raw = Ant.encode(msgId, data)
        logRaw(raw, true)
        if (!usb.write(raw)) onLine("WRITE FAILED msg 0x%02X".format(msgId))
        Thread.sleep(10)
    }

    /** Acknowledged data to the sensor on [channel]; returns true when the stick reports it delivered. */
    private fun sendAcknowledged(channel: Int, page: ByteArray): Boolean {
        txResult.remove(channel)
        send(Ant.ACK_DATA, byteArrayOf(channel.toByte()) + page)
        return waitFor(2500) { txResult.containsKey(channel) } && txResult[channel] == 5
    }

    private fun waitFor(timeoutMs: Long, cond: () -> Boolean): Boolean {
        val end = System.currentTimeMillis() + timeoutMs
        while (running && System.currentTimeMillis() < end) {
            if (cond()) return true
            Thread.sleep(25)
        }
        return cond()
    }

    private fun configureChannels() {
        // two power channels: the pedals may present as two devices
        listen(0, 120)
        listen(1, 11)
        listen(2, 11)
        listen(3, 121) // combined speed and cadence sensor
        listen(4, 122) // cadence-only sensor
        listen(5, 123) // speed-only sensor
        configured = true
    }

    // ---------- ride gating ----------
    /** True when a heart-rate signal arrived within the last 4 s. */
    fun heartRateLive(): Boolean =
        live[Metrics.HEART_RATE]?.let { System.currentTimeMillis() - it.second <= 4000 } == true

    @Volatile var rideActive = false
        private set

    /** Starts the ride (samples file, start marker). Refused while no heart rate is received. */
    fun beginRide(): Boolean {
        if (rideActive || !heartRateLive()) return false
        synchronized(logLock) {
            val now = System.currentTimeMillis()
            samplesWriter = samplesLog.bufferedWriter().also { it.write("ts_ms,devtype,devnum,metric,value\n") }
            rawWriter?.write("$now,WHEEL_MM,${(tracker.wheelM * 1000).toInt()}\n")
            rawWriter?.write("$now,RIDE_START\n")
            rawWriter?.flush()
            rideActive = true
        }
        return true
    }

    /** Wheel circumference in metres, used for speed and distance. */
    var wheelM: Double
        get() = tracker.wheelM
        set(v) { tracker.wheelM = v }

    private fun listen(channel: Int, devType: Int) {
        val period = Ant.DEVICE_TYPES.getValue(devType).second
        send(Ant.SET_NET_KEY, byteArrayOf(0) + Ant.NETWORK_KEY)
        send(Ant.ASSIGN_CH, byteArrayOf(channel.toByte(), 0x00, 0x00)) // 0x00 = receive channel
        setChannelId(channel, 0, devType)
        setPeriod(channel, period)
        send(Ant.SET_RF_FREQ, byteArrayOf(channel.toByte(), Ant.RF_FREQ_ANTPLUS.toByte()))
        // never give up searching: sensors (pedals) may start broadcasting minutes later
        send(Ant.SET_HP_SEARCH_TIMEOUT, byteArrayOf(channel.toByte(), 0x00)) // 0 = no high-priority phase
        send(Ant.SET_LP_SEARCH_TIMEOUT, byteArrayOf(channel.toByte(), 0xFF.toByte())) // 255 = infinite
        send(Ant.ENABLE_EXT, byteArrayOf(0x00, 0x01)) // extended data incl. sensor device number
        send(Ant.OPEN_CH, byteArrayOf(channel.toByte()))
        channelType[channel] = devType
        onLine("channel $channel listening for ${Ant.DEVICE_TYPES.getValue(devType).first}")
    }

    private fun setChannelId(channel: Int, devNum: Int, devType: Int) {
        val id = ByteBuffer.allocate(5).order(ByteOrder.LITTLE_ENDIAN)
            .put(channel.toByte()).putShort(devNum.toShort()).put(devType.toByte()).put(0).array()
        send(Ant.SET_CH_ID, id)
    }

    private fun setPeriod(channel: Int, period: Int) {
        val per = ByteBuffer.allocate(3).order(ByteOrder.LITTLE_ENDIAN)
            .put(channel.toByte()).putShort(period.toShort()).array()
        send(Ant.SET_PERIOD, per)
    }

    // ---------- cycling dynamics enabling (ANT+ Bicycle Power profile 4.5.2, 15.2.5) ----------
    private fun requestCapabilities(channel: Int): Boolean {
        // common page 0x46 request: descriptor 1 = subpage 0xFE, send the page 4 times, page 0x02
        return sendAcknowledged(channel, byteArrayOf(0x46, 0xFF.toByte(), 0xFF.toByte(), 0xFE.toByte(),
            0xFF.toByte(), 0x04, 0x02, 0x01))
    }

    private fun enableDynamicsFor(devNum: Int, channel: Int) {
        dynStatus = "dynamics: asking sensor $devNum for its capabilities"
        var c: IntArray? = null
        for (attempt in 1..3) {
            caps.remove(devNum)
            requestCapabilities(channel)
            if (waitFor(2500) { caps.containsKey(devNum) }) { c = caps[devNum]; break }
        }
        if (c == null) {
            dynStatus = "dynamics: sensor $devNum does not answer the capabilities request (not supported or silent)"
            return
        }
        val sMask = c[0]
        fun supported(bit: Int) = (sMask shr bit) and 1 == 0
        // display mask: bit 0 = apply; reserved bits 2 and 7 are always 1. Value: 1 = disable, 0 = enable.
        var mask = 0x84
        var value = 0x84
        val sup = ArrayList<String>()
        if (supported(0) && supported(1)) {
            mask = mask and 0x03.inv() // apply 4 Hz and 8 Hz bits together
            value = value or 0x01 // do not transmit at 4 Hz (bit 1 stays 0 = transmit at 8 Hz)
            sup += "8 Hz"
        }
        val names = mapOf(3 to "power phase", 4 to "PCO", 5 to "rider position", 6 to "torque barycenter")
        for ((bit, name) in names) {
            if (supported(bit)) {
                mask = mask and (1 shl bit).inv()
                value = value and (1 shl bit).inv() // 0 = enable
                sup += name
            }
        }
        if (sup.isEmpty()) {
            dynStatus = "dynamics: sensor $devNum supports no cycling dynamics (capabilities mask 0x%02X)".format(sMask)
            return
        }
        dynStatus = "dynamics: enabling ${sup.joinToString(", ")} on sensor $devNum"
        val page = byteArrayOf(0x02, 0xFE.toByte(), 0xFF.toByte(), 0xFF.toByte(), mask.toByte(), 0xFF.toByte(),
            value.toByte(), 0xFF.toByte())
        var ok = false
        for (attempt in 1..3) {
            if (sendAcknowledged(channel, page)) { ok = true; break }
        }
        // read back what the sensor now reports
        caps.remove(devNum)
        requestCapabilities(channel)
        waitFor(2500) { caps.containsKey(devNum) }
        val now = caps[devNum]
        val eightHz = now != null && (now[1] shr 1) and 1 == 0
        if (eightHz) {
            switchPeriod(channel, devNum, 4091) // 8 Hz
            dynStatus = "dynamics: ON for sensor $devNum (${sup.joinToString(", ")}), receiving at 8 Hz"
        } else {
            dynStatus = "dynamics: set command ${if (ok) "acknowledged" else "NOT acknowledged"} but sensor $devNum " +
                "does not report 8 Hz (value 0x%02X)".format(now?.get(1) ?: -1)
        }
    }

    private fun switchPeriod(channel: Int, devNum: Int, period: Int) {
        closedChannels.remove(channel)
        send(Ant.CLOSE_CH, byteArrayOf(channel.toByte()))
        waitFor(2000) { closedChannels.contains(channel) }
        setPeriod(channel, period)
        setChannelId(channel, devNum, 11)
        send(Ant.OPEN_CH, byteArrayOf(channel.toByte()))
    }

    // ---------- lifecycle ----------
    fun start() {
        running = true
        rawWriter = rawLog.bufferedWriter() // raw frames are logged from connect on, the ride from the marker
        // Reader: only ever reads and logs, so the stick's output queue can never back up.
        readerThread = Thread({
            try {
                while (running) handle(usb.read(100))
            } catch (e: Exception) {
                onLine("READER ERROR: ${e.message}")
            }
            synchronized(logLock) {
                try { rawWriter?.flush(); rawWriter?.close() } catch (_: Exception) {}
                try { samplesWriter?.flush(); samplesWriter?.close() } catch (_: Exception) {}
            }
            running = false
        }, "ant-reader").also { it.start() }
        // Setup: all writes (reset, channel configuration, dynamics enabling, watchdog re-configuration).
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
                        powerDevices.clear()
                        handled.clear()
                        Thread.sleep(300)
                        configureChannels()
                    }
                    if (enableDynamics) {
                        for ((devNum, ch) in powerDevices.entries.toList()) {
                            if (handled.add(devNum)) enableDynamicsFor(devNum, ch)
                        }
                    }
                    Thread.sleep(200)
                }
            } catch (e: InterruptedException) {
                // stop() interrupts us
            } catch (e: Exception) {
                onLine("SETUP ERROR: ${e.message}")
            }
        }, "ant-setup").also { it.start() }
    }

    private var lastFlush = 0L

    private fun handle(chunk: ByteArray) {
        val now = System.currentTimeMillis()
        if (chunk.isNotEmpty()) {
            for (f in parser.feed(chunk)) {
                synchronized(logLock) { rawWriter?.write("$now,${f.raw.hex().replace(" ", "")}\n") }
                process(f, now)
                describe(f)?.let { onLine(it) }
            }
        }
        if (now - lastFlush > 1000) { // keep the files current so a crash or pull loses at most 1 s
            synchronized(logLock) { rawWriter?.flush(); samplesWriter?.flush() }
            lastFlush = now
        }
    }

    private fun process(f: Frame, now: Long) {
        val d = f.data
        if (f.msgId == Ant.CHANNEL_EVENT && d.size >= 3 && d[1].toInt() == 0x01) {
            val ch = d[0].toInt() and 0xFF
            when (d[2].toInt() and 0xFF) {
                5, 6 -> txResult[ch] = d[2].toInt() and 0xFF
                7 -> closedChannels.add(ch)
            }
        }
        val b = Decoder.broadcast(f) { channelType[it] ?: 0 } ?: return
        val page = b.payload[0].toInt() and 0xFF
        if (b.devType == Decoder.DEV_POWER) {
            powerDevices[b.devNum] = b.channel
            if (page == 0x02 && (b.payload[1].toInt() and 0xFF) == 0xFE) {
                caps[b.devNum] = intArrayOf(b.payload[4].toInt() and 0xFF, b.payload[6].toInt() and 0xFF)
            }
            if (page in Decoder.DEDUPED_POWER_PAGES) {
                val key = "${b.devNum}:$page"
                if (lastPayload[key]?.contentEquals(b.payload) == true) return
                lastPayload[key] = b.payload
            }
        }
        for (s in tracker.decode(now, b.devType, b.devNum, b.payload)) {
            live[s.metric] = s.value to now
            synchronized(logLock) {
                samplesWriter?.write("${s.tsMs},${s.devType},${s.devNum},${s.metric},${s.value}\n")
            }
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
                val b = Decoder.broadcast(f) { channelType[it] ?: 0 } ?: return null
                lastDataMs = System.currentTimeMillis()
                val page = b.payload[0].toInt() and 0xFF
                val key = "${b.devType}:${b.devNum}:0x%02X".format(page)
                pageCounts.merge(key, 1, Int::plus)
                return "ch${b.channel} type=${b.devType} dev=${b.devNum}  ${b.payload.hex()}"
            }
        }
        return null
    }

    fun stop() {
        if (rideActive) synchronized(logLock) {
            rawWriter?.write("${System.currentTimeMillis()},RIDE_STOP\n")
            rawWriter?.flush()
        }
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
