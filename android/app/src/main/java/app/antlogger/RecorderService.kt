package app.antlogger

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentLinkedDeque

/**
 * Foreground service that owns the ANT stick session, so recording continues with the screen
 * off or the app in the background. The activity only starts/stops it and polls [session].
 */
class RecorderService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (session != null) return START_NOT_STICKY

        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Recording", NotificationManager.IMPORTANCE_LOW),
        )
        val notification = Notification.Builder(this, CHANNEL)
            .setContentTitle("ANT Logger")
            .setContentText("Recording ANT+ sensors")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(1, notification)
        }

        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "antlogger:recording")
            .also { it.acquire() }

        try {
            val usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
            val device = UsbAnt.find(usbManager) ?: error("No ANT stick found")
            val usb = UsbAnt.open(usbManager, device)
            val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
            val file = File(getExternalFilesDir(null), "capture_$stamp.csv")
            lines.clear()
            logFile = file
            val s = AntSession(usb, file) { line ->
                lines.addLast(line)
                while (lines.size > 120) lines.pollFirst()
            }
            session = s
            s.start()
        } catch (e: Exception) {
            lines.addLast("ERROR: ${e.message}")
            stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        session?.stop()
        session = null
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "recording"

        @Volatile var session: AntSession? = null
        @Volatile var logFile: File? = null
        val lines = ConcurrentLinkedDeque<String>()
    }
}
