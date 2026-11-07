package app.antlogger

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager

/** Port of ant/usbdev.py using the Android USB Host API (bulk IN/OUT, 64-byte packets). */
class UsbAnt private constructor(
    val device: UsbDevice,
    private val conn: UsbDeviceConnection,
    private val intf: UsbInterface,
    private val epIn: UsbEndpoint,
    private val epOut: UsbEndpoint,
) {
    private val readBuf = ByteArray(64)

    fun write(data: ByteArray): Boolean = conn.bulkTransfer(epOut, data, data.size, 1000) >= 0

    /** Returns the bytes read, or an empty array on timeout. */
    fun read(timeoutMs: Int = 100): ByteArray {
        val n = conn.bulkTransfer(epIn, readBuf, readBuf.size, timeoutMs)
        return if (n > 0) readBuf.copyOf(n) else ByteArray(0)
    }

    fun close() {
        try {
            conn.releaseInterface(intf)
        } catch (_: Exception) {
        }
        conn.close()
    }

    companion object {
        const val VID = 0x0FCF
        val PIDS = intArrayOf(0x1008, 0x1009) // USB Stick 2, USB-m

        fun find(manager: UsbManager): UsbDevice? =
            manager.deviceList.values.firstOrNull { it.vendorId == VID && it.productId in PIDS }

        fun open(manager: UsbManager, device: UsbDevice): UsbAnt {
            val conn = manager.openDevice(device) ?: error("openDevice failed (no permission?)")
            val intf = device.getInterface(0)
            check(conn.claimInterface(intf, true)) { "claimInterface failed" }
            var epIn: UsbEndpoint? = null
            var epOut: UsbEndpoint? = null
            for (i in 0 until intf.endpointCount) {
                val ep = intf.getEndpoint(i)
                if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                if (ep.direction == UsbConstants.USB_DIR_IN) epIn = ep else epOut = ep
            }
            checkNotNull(epIn) { "no bulk IN endpoint" }
            checkNotNull(epOut) { "no bulk OUT endpoint" }
            return UsbAnt(device, conn, intf, epIn, epOut)
        }
    }
}
