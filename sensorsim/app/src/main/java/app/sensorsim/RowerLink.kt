package app.sensorsim

import android.content.Context
import android.hardware.usb.UsbManager

/**
 * Reads the WaterRower S4 monitor on its own thread and feeds the lines to a [S4Decoder]. Finds the monitor, opens it
 * (the user must have granted USB permission in the app), sends the USB handshake and then only listens. When the
 * monitor is unplugged or fails, it waits and tries again.
 */
class RowerLink(private val ctx: Context, private val decoder: S4Decoder) {
    @Volatile var status = "USB rower: starting"
        private set
    @Volatile private var running = false
    private var thread: Thread? = null

    fun start() {
        if (running) return
        running = true
        thread = Thread({ loop() }, "s4-reader").also { it.start() }
    }

    fun stop() {
        running = false
        thread?.join(2000)
        thread = null
    }

    private fun nowSec() = System.nanoTime() / 1e9

    private fun pause(ms: Long) {
        try { Thread.sleep(ms) } catch (_: InterruptedException) { }
    }

    private fun loop() {
        val mgr = ctx.getSystemService(UsbManager::class.java)
        while (running) {
            val dev = UsbS4.find(mgr)
            if (dev == null) { status = "USB rower: monitor not found, plug it in"; pause(1000); continue }
            if (!mgr.hasPermission(dev)) { status = "USB rower: no USB permission, open the app and allow"; pause(1000); continue }
            val link = try {
                UsbS4.open(mgr, dev)
            } catch (e: Exception) {
                status = "USB rower: open failed (${e.message})"
                pause(2000)
                continue
            }
            try {
                session(link, mgr)
            } finally {
                link.close()
            }
        }
    }

    private fun session(link: UsbS4, mgr: UsbManager) {
        decoder.reset()
        val splitter = LineSplitter()
        val buf = ByteArray(256)
        status = "USB rower: connected, waiting for data"
        link.write(S4.HANDSHAKE)
        var handshakes = 1
        var bytes = 0L
        var lastData = nowSec()
        val start = lastData
        while (running) {
            val n = link.read(buf, 100)
            if (n > 0) {
                bytes += n
                lastData = nowSec()
                for (line in splitter.feed(buf, n)) decoder.onLine(line, lastData)
                status = "USB rower: streaming"
            } else {
                if (!mgr.deviceList.containsKey(link.device.deviceName)) { status = "USB rower: unplugged"; return }
                val now = nowSec()
                // the monitor sometimes needs the handshake again; the guard allows nothing but this
                if (bytes == 0L && handshakes < 3 && now - start > 3.0 * handshakes) { link.write(S4.HANDSHAKE); handshakes++ }
                if (bytes > 0 && now - lastData > 3.0) status = "USB rower: connected, no data for ${(now - lastData).toInt()} s"
            }
        }
    }
}
