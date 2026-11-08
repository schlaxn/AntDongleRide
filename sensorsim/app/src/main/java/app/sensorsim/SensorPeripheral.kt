package app.sensorsim

import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.Context
import android.os.Build
import android.os.Handler
import android.util.Log
import android.os.ParcelUuid
import java.util.UUID

private const val TAG = "SensorSim"

/** A characteristic the phone notifies, with its client configuration descriptor (CCCD). */
fun notifyChar(uuid: UUID, secure: Boolean) = BluetoothGattCharacteristic(uuid, BluetoothGattCharacteristic.PROPERTY_NOTIFY, 0).apply {
    // secure: turning notifications on needs an encrypted (paired) link; Android starts pairing when the client tries it.
    addDescriptor(BluetoothGattDescriptor(Uuids.CCCD,
        if (secure) BluetoothGattDescriptor.PERMISSION_READ_ENCRYPTED or BluetoothGattDescriptor.PERMISSION_WRITE_ENCRYPTED
        else BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE))
}

/** FTMS control point: written by the client, answered by an indication. */
fun controlPointChar(secure: Boolean) = BluetoothGattCharacteristic(
    Uuids.FTMS_CONTROL_POINT,
    BluetoothGattCharacteristic.PROPERTY_WRITE or BluetoothGattCharacteristic.PROPERTY_INDICATE,
    if (secure) BluetoothGattCharacteristic.PERMISSION_WRITE_ENCRYPTED else BluetoothGattCharacteristic.PERMISSION_WRITE,
).apply {
    addDescriptor(BluetoothGattDescriptor(Uuids.CCCD,
        if (secure) BluetoothGattDescriptor.PERMISSION_READ_ENCRYPTED or BluetoothGattDescriptor.PERMISSION_WRITE_ENCRYPTED
        else BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE))
}

/** A read-only characteristic; [secure] = reading needs an encrypted (paired) link. */
fun readChar(uuid: UUID, secure: Boolean = false) =
    BluetoothGattCharacteristic(uuid, BluetoothGattCharacteristic.PROPERTY_READ,
        if (secure) BluetoothGattCharacteristic.PERMISSION_READ_ENCRYPTED else BluetoothGattCharacteristic.PERMISSION_READ)

/**
 * One Bluetooth LE peripheral: a GATT server with the given services and a connectable advertisement. Here it is
 * the rower (FTMS + Device Information). Everything runs on the main thread (callbacks are posted there).
 */
class SensorPeripheral(
    private val ctx: Context,
    val label: String,
    private val services: List<BluetoothGattService>,  // the measurement service first, then Device Information
    private val notifyUuid: UUID,
    private val values: Map<UUID, ByteArray>,
    private val main: Handler,
    private val advertisedData: ByteArray? = null,  // service data put into the advertisement (FTMS needs it)
    private val onChange: () -> Unit,
) {
    private val service: BluetoothGattService get() = services.first()
    private val pending = ArrayDeque<BluetoothGattService>()

    var state = "Off"
        private set
    val failed: Boolean get() = state.startsWith("Failed")

    private var server: BluetoothGattServer? = null
    private var advertiser: BluetoothLeAdvertiser? = null
    private var advertising = false
    private var starting = false
    private val connected = LinkedHashSet<BluetoothDevice>()
    private val subscribed = LinkedHashSet<BluetoothDevice>()
    private val cpSubscribed = LinkedHashSet<BluetoothDevice>()  // control point indications (FTMS)

    val summary: String
        get() = buildString {
            append("$label: $state")
            if (connected.isNotEmpty()) append(", ${connected.size} connected")
            if (subscribed.isNotEmpty()) append(", ${subscribed.size} subscribed")
        }

    fun start() {
        val mgr = ctx.getSystemService(BluetoothManager::class.java)
        val adapter = mgr?.adapter
        if (mgr == null || adapter == null || !adapter.isEnabled) { fail("Bluetooth is off"); return }
        advertiser = adapter.bluetoothLeAdvertiser
        if (advertiser == null) { fail("no BLE advertising on this phone"); return }
        server = mgr.openGattServer(ctx, callback)
        if (server == null) { fail("cannot open GATT server"); return }
        state = "Adding services"
        pending.clear()
        pending.addAll(services)
        addNext()
    }

    /** Services are added one at a time; each onServiceAdded adds the next, and the last one starts advertising. */
    private fun addNext() {
        val s = pending.removeFirstOrNull()
        if (s == null) { startAdvertising(); return }
        if (server?.addService(s) != true) fail("addService failed")
    }

    fun stop() {
        try {
            if (advertising || starting) advertiser?.stopAdvertising(advCallback)
            server?.close()
        } catch (e: SecurityException) { /* permission gone: nothing more to do */ }
        server = null
        advertiser = null
        advertising = false
        starting = false
        connected.clear()
        subscribed.clear()
        cpSubscribed.clear()
        if (!failed) state = "Off"
    }

    private fun fail(msg: String) {
        state = "Failed: $msg"
        stop()
        onChange()
    }

    /** Notify every subscribed device with a new value. */
    fun send(value: ByteArray) {
        val s = server ?: return
        val c = service.getCharacteristic(notifyUuid) ?: return
        Log.i(TAG, "$label NOTIFY ${value.joinToString("") { "%02x".format(it) }} to ${subscribed.size} subscribers")
        for (d in subscribed.toList()) {
            try {
                if (Build.VERSION.SDK_INT >= 33) s.notifyCharacteristicChanged(d, c, false, value)
                else {
                    @Suppress("DEPRECATION")
                    c.value = value
                    @Suppress("DEPRECATION")
                    s.notifyCharacteristicChanged(d, c, false)
                }
            } catch (e: SecurityException) {
                fail("Bluetooth permission missing")
                return
            }
        }
    }

    /** FTMS control point: accept "request control" (0x00) and "reset" (0x01), refuse the rest; apps wait for the reply. */
    private fun indicateControlPoint(device: BluetoothDevice, c: BluetoothGattCharacteristic, op: Byte) {
        val s = server ?: return
        if (!cpSubscribed.contains(device)) return
        val reply = Ble.controlPointReply(op.toInt() and 0xFF)
        try {
            if (Build.VERSION.SDK_INT >= 33) s.notifyCharacteristicChanged(device, c, true, reply)
            else {
                @Suppress("DEPRECATION")
                c.value = reply
                @Suppress("DEPRECATION")
                s.notifyCharacteristicChanged(device, c, true)
            }
        } catch (e: SecurityException) {
            fail("Bluetooth permission missing")
        }
    }

    private fun startAdvertising() {
        val adv = advertiser ?: return
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(true)
            .setTimeout(0)
            .build()
        val dataBuilder = AdvertiseData.Builder()
            .setIncludeDeviceName(false)
            .addServiceUuid(ParcelUuid(service.uuid))
        advertisedData?.let { dataBuilder.addServiceData(ParcelUuid(service.uuid), it) }
        val data = dataBuilder.build()
        val scan = AdvertiseData.Builder().setIncludeDeviceName(true).build()
        starting = true
        try {
            adv.startAdvertising(settings, data, scan, advCallback)
        } catch (e: SecurityException) {
            starting = false
            fail("Bluetooth permission missing")
        }
    }

    /** Some phones stop connectable advertising while connected; start it again so another device can find us. */
    private fun restartAdvertising() {
        try {
            if (advertising || starting) advertiser?.stopAdvertising(advCallback)
        } catch (e: SecurityException) { /* ignore */ }
        advertising = false
        starting = false
        main.postDelayed({ if (server != null && !advertising && !starting) startAdvertising() }, 300)
    }

    private val advCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings) {
            Log.i(TAG, "$label advertising started")
            main.post {
                starting = false
                advertising = true
                state = "Advertising"
                onChange()
            }
        }

        override fun onStartFailure(errorCode: Int) {
            main.post {
                starting = false
                advertising = false
                fail("advertising error $errorCode")
            }
        }
    }

    private val callback = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, s: BluetoothGattService) {
            Log.i(TAG, "$label serviceAdded ${s.uuid} status=$status")
            main.post {
                if (server == null) return@post
                if (status == BluetoothGatt.GATT_SUCCESS) addNext() else fail("service add error $status")
            }
        }

        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            Log.i(TAG, "$label connection ${device.address} status=$status newState=$newState")
            main.post {
                // Android reports links that already exist (e.g. the watch's Garmin Connect link) when a server starts,
                // before we advertise. They are not clients of this sensor, so only count connections once advertising.
                if (server == null || state != "Advertising") return@post
                if (newState == BluetoothProfile.STATE_CONNECTED) connected.add(device)
                else {
                    connected.remove(device)
                    subscribed.remove(device)
                    cpSubscribed.remove(device)
                }
                if (state == "Advertising") restartAdvertising()
                onChange()
            }
        }

        override fun onCharacteristicReadRequest(device: BluetoothDevice, requestId: Int, offset: Int, c: BluetoothGattCharacteristic) {
            Log.i(TAG, "$label READ ${c.uuid} from ${device.address} offset=$offset known=${values.containsKey(c.uuid)}")
            main.post {
                val v = values[c.uuid]
                if (v == null) server?.sendResponse(device, requestId, BluetoothGatt.GATT_READ_NOT_PERMITTED, 0, null)
                else server?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, offset, v.copyOfRange(minOf(offset, v.size), v.size))
            }
        }

        override fun onDescriptorWriteRequest(
            device: BluetoothDevice, requestId: Int, d: BluetoothGattDescriptor, preparedWrite: Boolean,
            responseNeeded: Boolean, offset: Int, value: ByteArray,
        ) {
            Log.i(TAG, "$label WRITE descriptor ${d.uuid} of ${d.characteristic.uuid} from ${device.address} value=${value.joinToString("") { "%02x".format(it) }}")
            main.post {
                val on = value.isNotEmpty() && value[0].toInt() != 0
                val set = if (d.characteristic.uuid == notifyUuid) subscribed else cpSubscribed
                if (on) set.add(device) else set.remove(device)
                if (responseNeeded) server?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, null)
                onChange()
            }
        }

        override fun onDescriptorReadRequest(device: BluetoothDevice, requestId: Int, offset: Int, d: BluetoothGattDescriptor) {
            Log.i(TAG, "$label READ descriptor ${d.uuid} of ${d.characteristic.uuid} from ${device.address}")
            main.post {
                val on = (if (d.characteristic.uuid == notifyUuid) subscribed else cpSubscribed).contains(device)
                server?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, byteArrayOf(if (on) 1 else 0, 0))
            }
        }

        override fun onCharacteristicWriteRequest(
            device: BluetoothDevice, requestId: Int, c: BluetoothGattCharacteristic, preparedWrite: Boolean,
            responseNeeded: Boolean, offset: Int, value: ByteArray,
        ) {
            Log.i(TAG, "$label WRITE ${c.uuid} from ${device.address} value=${value.joinToString("") { "%02x".format(it) }}")
            main.post {
                val isControlPoint = c.uuid == Uuids.FTMS_CONTROL_POINT
                if (responseNeeded) server?.sendResponse(device, requestId,
                    if (isControlPoint) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
                if (isControlPoint && value.isNotEmpty()) indicateControlPoint(device, c, value[0])
            }
        }
    }
}
