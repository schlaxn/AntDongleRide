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
import java.io.File
import java.util.UUID
import kotlin.math.pow
import kotlin.math.roundToInt

/** What the user chose and what the service reports; shared between the screen and the service. */
object Hub {
    @Volatile var paceS = 131             // manual value: pace in seconds per 500 m (3:00 to 1:30); speed and power follow from it
    @Volatile var paceFactor = 1.0        // the measured (or manual) speed is multiplied by this (1/2 to 2); watts follow the result (cube: 1/8 to 8)
    @Volatile var strokeRate = 20.0
    @Volatile var paused = false
    @Volatile var useRower = false        // true: values come from the WaterRower S4 over USB
    @Volatile var bike = false            // true: pose as a power and cadence bike (Cycling Power only) instead of the rower
    @Volatile var cadenceFactor = 3.5     // bike: cadence = stroke rate x this (2 to 4)
    @Volatile var fields = 0              // optional Rower Data fields to send (RowerFields mask); 0 = the base layout
    @Volatile var fillLitres = 17.0       // the monitor's fill setting; it only scales the watts
    @Volatile var running = false
    @Volatile var status = "Stopped"
    @Volatile var rowerStatus = ""
    @Volatile var sent = ""
    @Volatile var lastFit = ""            // name of the .fit file written when the last session stopped
}

/**
 * Foreground service. In rower mode it runs the FTMS rower (for MyWhoosh rowing) and, alongside it, a Cycling Power sensor
 * with the same power, so an app that takes power from a power meter gets it too. In bike mode the Cycling Power sensor is
 * the bike. Values come from the WaterRower S4 over USB ([RowerLink] + [S4Decoder]) or, for testing, from the manual sliders
 * ([ManualRower]). Every session is recorded and written to a .fit file when it stops ([ActivityRecorder]).
 */
class SensorService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var peripheral: SensorPeripheral? = null   // the FTMS rower (rower mode only)
    private var power: SensorPeripheral? = null        // the Cycling Power sensor (both modes)
    private val manual = ManualRower()
    private val decoder = S4Decoder()
    private val stats = SessionStats()
    private val recorder = ActivityRecorder()
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
            .setContentText("Posing as $what for MyWhoosh, recording the session")
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
        tick = 0
        Hub.lastFit = ""
        val cps = powerPeripheral()
        power = cps
        val rower = if (Hub.bike) null else rowerPeripheral()
        peripheral = rower
        rower?.start()
        cps.start()
        refresh()
        val all = listOfNotNull(rower, cps)
        if (all.all { it.failed }) {
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

    /** The Cycling Power sensor: power and crank cadence (the bike, or the power source next to the rower). */
    private fun powerPeripheral(): SensorPeripheral {
        val dis = deviceInfo(if (Hub.bike) "Bike sensor" else "Power sensor")
        return SensorPeripheral(
            this, "Power", listOf(powerService(), dis.first), Uuids.CPS_MEASUREMENT,
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
            Uuids.FIRMWARE to Ble.text("0.5"),
        )
    }

    private fun ftmsService(): BluetoothGattService {
        val s = BluetoothGattService(Uuids.FTMS, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        s.addCharacteristic(notifyChar(Uuids.ROWER_DATA, false))
        s.addCharacteristic(readChar(Uuids.FTMS_FEATURE))
        s.addCharacteristic(controlPointChar(false))
        s.addCharacteristic(notifyChar(Uuids.FTMS_STATUS, false))   // spec: mandatory when the control point exists
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
                // the measured speed (15 s average) times the speed factor; watts from the result, as the cube law gives them
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
            val cadence = if (Hub.bike) sendBike(snap) else sendRower(snap)
            if (tick % 4 == 0) {
                recorder.add(ActivityRecorder.Point(System.currentTimeMillis(), snap.powerW, cadence, snap.speedMps, snap.distanceM))
            }
            tick++
            refresh()
            main.postDelayed(this, TICK_MS)
        }
    }

    /** Rower: the FTMS packet once a second, and the Cycling Power sensor (power, stroke count as crank revolutions) 4 Hz. */
    private fun sendRower(snap: RowSnapshot): Double {
        val packet = Ble.rowerData(snap, Hub.fields)
        if (tick % 4 == 0) peripheral?.send(packet)
        power?.send(Ble.cpsMeasurement(snap.powerW, snap.strokeCount, snap.crankTicks))
        val pace = Ble.paceS500(snap.speedMps)
        Hub.sent = "${"%.1f".format(snap.strokeRateSpm)} spm, ${snap.powerW} W, " +
            (if (pace == 0) "-- /500 m" else "%d:%02d /500 m".format(pace / 60, pace % 60)) +
            ", ${snap.distanceM.toInt()} m, ${snap.strokeCount} strokes" +
            "\nRower packet: ${packet.size} bytes, flags 0x%04X".format((packet[0].toInt() and 0xFF) or ((packet[1].toInt() and 0xFF) shl 8))
        return snap.strokeRateSpm
    }

    /** Bike: a Cycling Power sensor (power and crank cadence) at 4 Hz, like a pedal. Cadence = stroke rate x the cadence factor. */
    private fun sendBike(snap: RowSnapshot): Double {
        val cadence = if (Hub.paused) 0.0 else snap.strokeRateSpm * Hub.cadenceFactor
        bikeClockMs += TICK_MS
        bikeCrank.advance(cadence / 60.0, bikeClockMs, TICK_MS.toDouble())
        val packet = Ble.cpsMeasurement(snap.powerW, bikeCrank.count, bikeCrank.eventTicks)
        power?.send(packet)   // 4 Hz, like a power meter
        Hub.sent = "%.1f rpm (cadence), %d W".format(cadence, snap.powerW) +
            "\nBike packet: ${packet.size} bytes, crank revs ${bikeCrank.count}"
        return cadence
    }

    /** The status line: the Bluetooth sensors, the USB rower when it is the source, and the last saved .fit file. */
    private fun refresh() {
        val parts = listOfNotNull(peripheral, power)
        Hub.status = when {
            parts.isEmpty() -> "Stopped"
            !Hub.running && parts.none { it.failed } -> "Stopped"
            else -> parts.joinToString("  |  ") { it.summary } +
                (link?.let { "  |  ${it.status}" } ?: "") +
                (if (Hub.lastFit.isNotEmpty()) "  |  saved ${Hub.lastFit}" else "")
        }
    }

    /** Stops everything and writes the recorded session to a .fit file in the app's own folder. */
    private fun shutdown() {
        Hub.running = false
        ticking = false
        main.removeCallbacksAndMessages(null)
        link?.stop()
        link = null
        peripheral?.stop()
        power?.stop()
        saveSession()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    private fun saveSession() {
        val stamp = java.text.SimpleDateFormat("yyyy-MM-dd_HHmmss", java.util.Locale.US).format(java.util.Date())
        val dir = getExternalFilesDir("fit") ?: File(filesDir, "fit")
        val name = "sim_${if (Hub.bike) "bike" else "rower"}_$stamp.fit"
        try {
            val f = recorder.write(File(dir, name), rower = !Hub.bike)
            if (f != null) Hub.lastFit = f.name
        } catch (e: Exception) {
            Hub.lastFit = "not saved (${e.message})"
        }
    }

    companion object {
        const val ACTION_STOP = "app.sensorsim.STOP"
        const val TICK_MS = 250L
    }
}
