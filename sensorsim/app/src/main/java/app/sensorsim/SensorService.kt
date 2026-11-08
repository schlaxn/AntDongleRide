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

/** What the user chose and what the service reports; shared between the screen and the service. */
object Hub {
    @Volatile var powerW = 100            // manual values, used when the USB rower is off; speed follows from the power
    @Volatile var strokeRate = 20.0
    @Volatile var paused = false
    @Volatile var useRower = false        // true: values come from the WaterRower S4 over USB
    @Volatile var cps = true              // also send the power as a Cycling Power sensor (the path that worked for power)
    @Volatile var fillLitres = 17.0       // the monitor's fill setting; it only scales the watts
    @Volatile var running = false
    @Volatile var status = "Stopped"
    @Volatile var rowerStatus = ""
    @Volatile var sent = ""
}

/**
 * Foreground service: one Bluetooth LE peripheral posing as an FTMS rower for MyWhoosh. Its values come from the
 * WaterRower S4 over USB ([RowerLink] + [S4Decoder]) or, for testing, from the manual sliders ([ManualRower]).
 */
class SensorService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var peripheral: SensorPeripheral? = null
    private var power: SensorPeripheral? = null
    private val manual = ManualRower()
    private val decoder = S4Decoder()
    private val stats = SessionStats()
    private var link: RowerLink? = null
    private var ticking = false
    private var tick = 0

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
        nm.createNotificationChannel(NotificationChannel("sim", "Rower simulator", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, "sim")
            .setContentTitle("BLE Sensor Sim")
            .setContentText("Posing as a rower for MyWhoosh")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun start() {
        val dis = deviceInfo("Rower")
        val p = SensorPeripheral(
            this, "Rower", listOf(ftmsService(), dis.first), Uuids.ROWER_DATA,
            mapOf(Uuids.FTMS_FEATURE to Ble.ftmsFeature()) + dis.second, main,
            // FTMS service data: flags (machine available) + machine type, little endian (rower = bit 4 = 0x0010;
            // indoor bike would be bit 5 = 0x0020)
            advertisedData = byteArrayOf(0x01, 0x10, 0x00),
        ) { refresh() }
        peripheral = p
        if (Hub.cps) {
            val pdis = deviceInfo("Power sensor")
            power = SensorPeripheral(
                this, "Power", listOf(powerService(), pdis.first), Uuids.CPS_MEASUREMENT,
                mapOf(Uuids.CPS_FEATURE to Ble.cpsFeature(), Uuids.SENSOR_LOCATION to Ble.sensorLocation()) + pdis.second,
                main,
            ) { refresh() }
        }
        if (Hub.useRower) {
            decoder.reset()
            link = RowerLink(this, decoder).also { it.start() }
        }
        p.start()
        power?.start()
        refresh()
        if (p.failed && (power == null || power!!.failed)) {
            shutdown()
            refresh()
            stopSelf()
            return
        }
        ticking = true
        main.post(ticker)
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
            Uuids.FIRMWARE to Ble.text("0.3"),
        )
    }

    private fun powerService(): BluetoothGattService {
        val s = BluetoothGattService(Uuids.CYCLING_POWER, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        s.addCharacteristic(notifyChar(Uuids.CPS_MEASUREMENT, false))
        s.addCharacteristic(readChar(Uuids.CPS_FEATURE))
        s.addCharacteristic(readChar(Uuids.SENSOR_LOCATION))
        return s
    }

    private fun ftmsService(): BluetoothGattService {
        val s = BluetoothGattService(Uuids.FTMS, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        s.addCharacteristic(notifyChar(Uuids.ROWER_DATA, false))
        s.addCharacteristic(readChar(Uuids.FTMS_FEATURE))
        s.addCharacteristic(controlPointChar(false))
        return s
    }

    private val ticker = object : Runnable {
        override fun run() {
            if (!Hub.running) return
            decoder.fillLitres = Hub.fillLitres
            val raw = if (Hub.useRower) {
                decoder.snapshot(System.nanoTime() / 1e9)
            } else {
                val t = if (Hub.paused) Targets(0, 0.0) else Targets(Hub.powerW, Hub.strokeRate)
                manual.advance(t, TICK_MS.toDouble(), Hub.fillLitres)
                manual.snapshot()
            }
            stats.add(raw, TICK_MS / 1000.0)
            val snap = stats.extend(raw)
            if (tick % 4 == 0) peripheral?.send(Ble.rowerData(snap))
            power?.send(Ble.cpsMeasurement(snap.powerW, snap.strokeCount, snap.crankTicks))   // 4 Hz, like a power meter
            tick++
            val pace = Ble.paceS500(snap.speedMps)
            Hub.sent = "${"%.1f".format(snap.strokeRateSpm)} spm, ${snap.powerW} W, " +
                (if (pace == 0) "-- /500 m" else "%d:%02d /500 m".format(pace / 60, pace % 60)) +
                ", ${snap.distanceM.toInt()} m, ${snap.strokeCount} strokes"
            refresh()
            main.postDelayed(this, TICK_MS)
        }
    }

    /** The status line: the Bluetooth side, plus the USB rower when it is the source. */
    private fun refresh() {
        val p = peripheral
        val parts = listOfNotNull(p, power)
        Hub.status = when {
            p == null -> "Stopped"
            !Hub.running && parts.none { it.failed } -> "Stopped"
            else -> parts.joinToString("  |  ") { it.summary } + (link?.let { "  |  ${it.status}" } ?: "")
        }
    }

    private fun shutdown() {
        Hub.running = false
        ticking = false
        main.removeCallbacksAndMessages(null)
        link?.stop()
        link = null
        peripheral?.stop()
        power?.stop()
        power = null
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    companion object {
        const val ACTION_STOP = "app.sensorsim.STOP"
        const val TICK_MS = 250L
    }
}
