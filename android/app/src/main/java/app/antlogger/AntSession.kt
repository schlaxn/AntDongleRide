package app.antlogger

import java.io.File
import java.io.Writer
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentHashMap

/**
 * Owns the stick. READ-ONLY toward sensors: the app only configures its own receive channels on the
 * stick; it never transmits anything to a sensor (see [Ant.requireReadOnly], enforced in UsbAnt.write).
 *
 * Two threads: the reader only reads/decodes/logs (so the stick's output queue never backs up), the setup
 * thread does the stick's own configuration (reset, receive channels, watchdog re-configuration).
 *
 * Logged per session: raw frames (`ts_ms,hex`, and `ts_ms,hex,T` for the configuration frames we send to
 * the stick) and, per ride, every decoded sample (`ts_ms,devtype,devnum,metric,value`).
 * The raw log is the source of truth.
 */
class AntSession(
    private val usb: UsbAnt,
    private val rawLog: File,
    private val newSamplesFile: () -> File, // one sample file per ride
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

    private val lastPayload = ConcurrentHashMap<String, ByteArray>()

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

    // ---------- configuring the stick (setup thread only) ----------
    private fun logRaw(raw: ByteArray, tx: Boolean) {
        synchronized(logLock) {
            rawWriter?.write("${System.currentTimeMillis()},${raw.hex().replace(" ", "")}${if (tx) ",T" else ""}\n")
        }
    }

    /** Sends a configuration message to the stick itself; the read-only guard in UsbAnt.write checks it. */
    private fun send(msgId: Int, data: ByteArray = ByteArray(0)) {
        val raw = Ant.encode(msgId, data)
        Ant.requireReadOnly(raw)
        logRaw(raw, true)
        if (!usb.write(raw)) onLine("WRITE FAILED msg 0x%02X".format(msgId))
        Thread.sleep(10)
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

    // ---------- ride gating ----------
    /** True when a heart-rate signal arrived within the last 4 s. */
    fun heartRateLive(): Boolean =
        live[Metrics.HEART_RATE]?.let { System.currentTimeMillis() - it.second <= 4000 } == true

    @Volatile var rideActive = false
        private set
    @Volatile var rideStartMs = 0L
        private set

    /** Starts a ride (own samples file, start marker). Refused while no heart rate is received. */
    fun beginRide(): Boolean {
        if (rideActive || !heartRateLive()) return false
        synchronized(logLock) {
            val now = System.currentTimeMillis()
            samplesWriter = newSamplesFile().bufferedWriter().also { it.write("ts_ms,devtype,devnum,metric,value\n") }
            rawWriter?.write("$now,WHEEL_MM,${(tracker.wheelM * 1000).toInt()}\n")
            rawWriter?.write("$now,RIDE_START\n")
            rawWriter?.flush()
            tracker.requestDistanceReset() // distance counts from the start of the ride
            rideStartMs = now
            rideActive = true
        }
        return true
    }

    /** Ends the ride; the stick stays connected and the live display keeps running. */
    fun endRide() {
        synchronized(logLock) {
            if (!rideActive) return
            rawWriter?.write("${System.currentTimeMillis()},RIDE_STOP\n")
            rawWriter?.flush()
            try { samplesWriter?.flush(); samplesWriter?.close() } catch (_: Exception) {}
            samplesWriter = null
            rideActive = false
        }
    }

    /** Wheel circumference in metres, used for speed and distance. */
    var wheelM: Double
        get() = tracker.wheelM
        set(v) { tracker.wheelM = v }

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
        // Setup: the stick's own configuration (reset, receive channels, watchdog re-configuration).
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
        val b = Decoder.broadcast(f) { channelType[it] ?: 0 } ?: return
        val page = b.payload[0].toInt() and 0xFF
        if (b.devType == Decoder.DEV_POWER && page in Decoder.DEDUPED_POWER_PAGES) {
            val key = "${b.devNum}:$page"
            if (lastPayload[key]?.contentEquals(b.payload) == true) return
            lastPayload[key] = b.payload
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
        endRide()
        running = false
        setupThread?.interrupt()
        setupThread?.join(2000)
        readerThread?.join(2000)
        try {
            // leave the stick clean so the next session does not inherit open channels
            val reset = Ant.encode(Ant.RESET, byteArrayOf(0))
            Ant.requireReadOnly(reset)
            usb.write(reset)
        } catch (_: Exception) {
        }
        usb.close()
    }
}
