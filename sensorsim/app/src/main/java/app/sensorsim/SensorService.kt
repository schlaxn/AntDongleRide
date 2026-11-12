package app.sensorsim

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothGattService
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import java.util.UUID
import kotlin.math.pow
import kotlin.math.roundToInt

/** What the user chose and what the service reports; shared between the screen and the service. */
object Hub {
    @Volatile var paceS = 131             // manual value: pace in seconds per 500 m (3:00 to 1:30); speed and power follow from it
    @Volatile var paceFactor = 1.0        // USB mode: the measured speed is multiplied by this (1/8 to 8); watts follow the result
    @Volatile var strokeRate = 20.0
    @Volatile var paused = false
    @Volatile var useRower = false        // true: values come from the WaterRower S4 over USB
    @Volatile var bike = false            // true: pose as a power and cadence bike (Cycling Power) instead of the rower
    @Volatile var cadenceFactor = 3.5     // bike: cadence = stroke rate x this (2 to 4)
    @Volatile var fields = 0              // optional Rower Data fields to send (RowerFields mask); 0 = the base layout
    @Volatile var fillLitres = 17.0       // the monitor's fill setting; it only scales the watts
    @Volatile var running = false
    @Volatile var status = "Stopped"
    @Volatile var rowerStatus = ""
    @Volatile var sent = ""
}

/**
 * Foreground service: one Bluetooth LE peripheral, either an FTMS rower (for MyWhoosh rowing) or a Cycling Power bike
 * sensor (power and cadence). Its values come from the WaterRower S4 over USB ([RowerLink] + [S4Decoder]) or, for testing,
 * from the manual sliders ([ManualRower]).
 */
class SensorService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var peripheral: SensorPeripheral? = null
    private val manual = ManualRower()
    private val decoder = S4Decoder()
    private val stats = SessionStats()
    private var link: RowerLink? = null
    private var ticking = false
    private var tick = 0
    private var spoofDistanceM = 0.0   // USB mode: distance integrated from the spoofed speed, so it matches the speed shown
    private var bikeCrank = RevCounter()   // bike: crank revolutions at the bike cadence
    private var bikeClockMs = 0.0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutdown()
            Hub.status = "Stopped"
            stopSelf()
            return START_NOT_STICKY
        }
        if (Hub.running) return START_NOT_STICKY
        startForeground(1, notification())
        Hub.running = true
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sensorsim:ble").apply { acquire() }
        start()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        shutdown()
        refresh()
        super.onDestroy()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("sim", "Sensor simulator", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val what = if (Hub.bike) "a power and cadence bike" else "a rower"
        return Notification.Builder(this, "sim")
            .setContentTitle("BLE Sensor Sim")
            .setContentText("Posing as $what for MyWhoosh")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun start() {
        if (Hub.useRower) {
            decoder.reset()
            link = RowerLink(this, decoder).also { it.start() }
        }
        spoofDistanceM = 0.0
        bikeCrank = RevCounter()
        bikeClockMs = 0.0
        val p = if (Hub.bike) bikePeripheral() else rowerPeripheral()
        peripheral = p
        p.start()
        refresh()
        if (p.failed) {
            shutdown()
            refresh()
            stopSelf()
            return
        }
        ticking = true
        main.post(ticker)
    }

    /** The FTMS rower: the profile MyWhoosh rows with. */
    private fun rowerPeripheral(): SensorPeripheral {
        val dis = deviceInfo("Rower")
        return SensorPeripheral(
            this, "Rower", listOf(ftmsService(), dis.first), Uuids.ROWER_DATA,
            mapOf(Uuids.FTMS_FEATURE to Ble.ftmsFeature(Hub.fields)) + dis.second, main,
            // FTMS service data: flags (machine available) + machine type, little endian (rower = bit 4 = 0x0010)
            advertisedData = byteArrayOf(0x01, 0x10, 0x00),
        ) { refresh() }
    }

    /** The Cycling Power bike sensor: power and crank cadence. */
    private fun bikePeripheral(): SensorPeripheral {
        val dis = deviceInfo("Bike sensor")
        return SensorPeripheral(
            this, "Bike", listOf(powerService(), dis.first), Uuids.CPS_MEASUREMENT,
            mapOf(Uuids.CPS_FEATURE to Ble.cpsFeature(), Uuids.SENSOR_LOCATION to Ble.sensorLocation()) + dis.second, main,
        ) { refresh() }
    }

    /** Device Information (0x180A): manufacturer, model and firmware text that apps show or check. */
    private fun deviceInfo(model: String): Pair<BluetoothGattService, Map<UUID, ByteArray>> {
        val s = BluetoothGattService(Uuids.DEVICE_INFO, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        s.addCharacteristic(readChar(Uuids.MANUFACTURER))
        s.addCharacteristic(readChar(Uuids.MODEL))
        s.addCharacteristic(readChar(Uuids.FIRMWARE))
        return s to mapOf(
            Uuids.MANUFACTURER to Ble.text("BLE Sensor Sim"),
            Uuids.MODEL to Ble.text(model),
            Uuids.FIRMWARE to Ble.text("0.4"),
        )
    }

    private fun ftmsService(): BluetoothGattService {
        val s = BluetoothGattService(Uuids.FTMS, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        s.addCharacteristic(notifyChar(Uuids.ROWER_DATA, false))
        s.addCharacteristic(readChar(Uuids.FTMS_FEATURE))
        s.addCharacteristic(controlPointChar(false))
        return s
    }

    private fun powerService(): BluetoothGattService {
        val s = BluetoothGattService(Uuids.CYCLING_POWER, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        s.addCharacteristic(notifyChar(Uuids.CPS_MEASUREMENT, false))
        s.addCharacteristic(readChar(Uuids.CPS_FEATURE))
        s.addCharacteristic(readChar(Uuids.SENSOR_LOCATION))
        return s
    }

    private val ticker = object : Runnable {
        override fun run() {
            if (!Hub.running) return
            decoder.fillLitres = Hub.fillLitres
            val raw = if (Hub.useRower) {
                // the measured speed (15 s average) times the pace factor; watts from the result, as the cube law gives them
                val m = decoder.snapshot(System.nanoTime() / 1e9)
                val v = if (Hub.paused) 0.0 else m.speedMps * Hub.paceFactor
                spoofDistanceM += v * TICK_MS / 1000.0
                m.copy(
                    speedMps = v,
                    powerW = if (v > 0) (S4.WATT_CONST * v.pow(3) * S4.fillFactor(Hub.fillLitres)).roundToInt() else 0,
                    distanceM = spoofDistanceM,
                )
            } else {
                // the speed factor applies to the manual pace too: speed = 500 / pace x factor
                val t = if (Hub.paused) Targets(0.0, 0.0) else Targets(500.0 / Hub.paceS * Hub.paceFactor, Hub.strokeRate)
                manual.advance(t, TICK_MS.toDouble(), Hub.fillLitres)
                manual.snapshot()
            }
            stats.add(raw, TICK_MS / 1000.0)
            val snap = stats.extend(raw)
            if (Hub.bike) sendBike(snap) else sendRower(snap)
            tick++
            refresh()
            main.postDelayed(this, TICK_MS)
        }
    }

    private fun sendRower(snap: RowSnapshot) {
        val packet = Ble.rowerData(snap, Hub.fields)
        if (tick % 4 == 0) peripheral?.send(packet)
        val pace = Ble.paceS500(snap.speedMps)
        Hub.sent = "${"%.1f".format(snap.strokeRateSpm)} spm, ${snap.powerW} W, " +
            (if (pace == 0) "-- /500 m" else "%d:%02d /500 m".format(pace / 60, pace % 60)) +
            ", ${snap.distanceM.toInt()} m, ${snap.strokeCount} strokes" +
            "\nRower packet: ${packet.size} bytes, flags 0x%04X".format((packet[0].toInt() and 0xFF) or ((packet[1].toInt() and 0xFF) shl 8))
    }

    /** Bike: power is the same value the rower sends; cadence = stroke rate x the cadence factor, sent as crank revolutions. */
    private fun sendBike(snap: RowSnapshot) {
        val cadence = if (Hub.paused) 0.0 else snap.strokeRateSpm * Hub.cadenceFactor
        bikeClockMs += TICK_MS
        bikeCrank.advance(cadence / 60.0, bikeClockMs, TICK_MS.toDouble())
        val packet = Ble.cpsMeasurement(snap.powerW, bikeCrank.count, bikeCrank.eventTicks)
        peripheral?.send(packet)   // 4 Hz, like a power meter
        Hub.sent = "%.1f rpm (cadence), %d W".format(cadence, snap.powerW) +
            "\nBike packet: ${packet.size} bytes, crank revs ${bikeCrank.count}"
    }

    /** The status line: the Bluetooth side, plus the USB rower when it is the source. */
    private fun refresh() {
        val p = peripheral
        Hub.status = when {
            p == null -> "Stopped"
            !Hub.running && !p.failed -> "Stopped"
            else -> p.summary + (link?.let { "  |  ${it.status}" } ?: "")
        }
    }

    private fun shutdown() {
        Hub.running = false
        ticking = false
        main.removeCallbacksAndMessages(null)
        link?.stop()
        link = null
        peripheral?.stop()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    companion object {
        const val ACTION_STOP = "app.sensorsim.STOP"
        const val TICK_MS = 250L
    }
}
