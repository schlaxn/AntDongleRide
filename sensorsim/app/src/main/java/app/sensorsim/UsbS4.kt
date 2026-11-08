package app.sensorsim

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager

/**
 * The WaterRower S4 monitor as a USB CDC serial device (Microchip 04D8:000A), driven directly with the Android USB
 * Host API: line coding 115200 8N1, DTR/RTS on, bulk IN/OUT. No serial library needed.
 */
class UsbS4 private constructor(
    val device: UsbDevice,
    private val conn: UsbDeviceConnection,
    private val interfaces: List<UsbInterface>,
    private val epIn: UsbEndpoint,
    private val epOut: UsbEndpoint,
) {
    /** The single choke point for everything sent to the monitor: only the USB handshake passes. */
    fun write(data: ByteArray): Boolean {
        S4.requireHandshakeOnly(data)
        return conn.bulkTransfer(epOut, data, data.size, 1000) >= 0
    }

    /** Bytes read into [buf], or a negative number on timeout or error. */
    fun read(buf: ByteArray, timeoutMs: Int): Int = conn.bulkTransfer(epIn, buf, buf.size, timeoutMs)

    fun close() {
        for (i in interfaces) try { conn.releaseInterface(i) } catch (_: Exception) {}
        conn.close()
    }

    companion object {
        const val VID = 0x04D8
        const val PID = 0x000A

        fun find(manager: UsbManager): UsbDevice? =
            manager.deviceList.values.firstOrNull { it.vendorId == VID && it.productId == PID }

        fun open(manager: UsbManager, device: UsbDevice): UsbS4 {
            val conn = manager.openDevice(device) ?: error("openDevice failed (no permission?)")
            var comm: UsbInterface? = null
            var data: UsbInterface? = null
            for (i in 0 until device.interfaceCount) {
                val itf = device.getInterface(i)
                if (itf.interfaceClass == UsbConstants.USB_CLASS_COMM) comm = itf
                if (itf.interfaceClass == UsbConstants.USB_CLASS_CDC_DATA) data = itf
            }
            val dataIf = data ?: error("no CDC data interface")
            val claimed = ArrayList<UsbInterface>()
            try {
                if (comm != null) {
                    check(conn.claimInterface(comm, true)) { "claim control interface failed" }
                    claimed += comm
                }
                check(conn.claimInterface(dataIf, true)) { "claim data interface failed" }
                claimed += dataIf
                var epIn: UsbEndpoint? = null
                var epOut: UsbEndpoint? = null
                for (i in 0 until dataIf.endpointCount) {
                    val ep = dataIf.getEndpoint(i)
                    if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                    if (ep.direction == UsbConstants.USB_DIR_IN) epIn = ep else epOut = ep
                }
                checkNotNull(epIn) { "no bulk IN endpoint" }
                checkNotNull(epOut) { "no bulk OUT endpoint" }
                val index = comm?.id ?: 0
                // SET_LINE_CODING: 115200 baud (0x0001C200 little endian), 1 stop bit, no parity, 8 data bits
                val coding = byteArrayOf(0x00, 0xC2.toByte(), 0x01, 0x00, 0x00, 0x00, 0x08)
                conn.controlTransfer(0x21, 0x20, 0, index, coding, coding.size, 1000)
                // SET_CONTROL_LINE_STATE: DTR + RTS
                conn.controlTransfer(0x21, 0x22, 0x03, index, null, 0, 1000)
                return UsbS4(device, conn, claimed, epIn, epOut)
            } catch (e: Exception) {
                for (i in claimed) try { conn.releaseInterface(i) } catch (_: Exception) {}
                conn.close()
                throw e
            }
        }
    }
}
