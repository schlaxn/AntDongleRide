package app.antlogger

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import java.io.File

/** Phase-1 spike screen: starts/stops the recording service and shows what it receives. */
class MainActivity : Activity() {
    private lateinit var usbManager: UsbManager
    private lateinit var status: TextView
    private lateinit var summary: TextView
    private lateinit var logView: TextView
    private lateinit var button: Button
    private lateinit var dimButton: Button
    private lateinit var dynStatusView: TextView
    private lateinit var recordButton: Button
    private lateinit var liveView: LiveView
    private val ui = Handler(Looper.getMainLooper())

    private val permissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != ACTION_PERMISSION) return
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            if (granted) startRecording() else status.text = "USB permission denied"
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            val s = RecorderService.session
            if (s != null) {
                val age = if (s.lastDataMs == 0L) "never" else "${(System.currentTimeMillis() - s.lastDataMs) / 1000}s ago"
                val pages = s.pageCounts.entries.sortedBy { it.key }
                    .joinToString("\n") { "${it.key}  x${it.value}" }
                    .ifEmpty { "no broadcast pages yet" }
                summary.text = "last data: $age   stick resets: ${s.resets}\n$pages"
                status.text = (if (s.rideActive) "RECORDING ride, " else "connected, not recording, ") +
                    "raw log ${RecorderService.logFile?.name}"
                // disconnecting mid-ride would end the ride by accident: stop recording first
                button.isEnabled = !s.rideActive
                button.text = if (s.rideActive) "Disconnect (stop recording first)" else "Disconnect"
                dynStatusView.text = s.dynStatus
                recordButton.isEnabled = s.rideActive || s.heartRateLive()
                recordButton.text = when {
                    s.rideActive -> "Stop recording"
                    s.heartRateLive() -> "Start recording"
                    else -> "Start recording (waiting for heart rate...)"
                }
            } else {
                button.isEnabled = true
                button.text = "Connect"
                recordButton.isEnabled = false
                recordButton.text = "Start recording (connect first)"
            }
            liveView.invalidate()
            logView.text = RecorderService.lines.toList().takeLast(40).joinToString("\n")
            ui.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // true black everywhere: window, status bar and navigation bar (screen stays on while recording)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(android.graphics.Color.BLACK))
        window.statusBarColor = android.graphics.Color.BLACK
        window.navigationBarColor = android.graphics.Color.BLACK

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 48, 24, 24) }
        status = TextView(this).apply { textSize = 16f; text = "idle" }
        button = Button(this).apply { text = "Connect"; setOnClickListener { onButton() } }
        recordButton = Button(this).apply {
            text = "Start recording (connect first)"
            isEnabled = false
            setOnClickListener {
                val s = RecorderService.session ?: return@setOnClickListener
                if (s.rideActive) s.endRide() else s.beginRide()
            }
        }
        val wheelPrefs = getSharedPreferences("settings", Context.MODE_PRIVATE)
        val wheelEdit = android.widget.EditText(this).apply {
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            hint = "Wheel circumference in mm (speed sensor)"
            setText(wheelPrefs.getInt("wheel_mm", 2105).toString())
            addTextChangedListener(object : android.text.TextWatcher {
                override fun afterTextChanged(s: android.text.Editable?) {
                    val mm = s?.toString()?.toIntOrNull() ?: return
                    if (mm in 1000..3000) {
                        wheelPrefs.edit().putInt("wheel_mm", mm).apply()
                        RecorderService.session?.wheelM = mm / 1000.0
                    }
                }
                override fun beforeTextChanged(s: CharSequence?, st: Int, c: Int, a: Int) {}
                override fun onTextChanged(s: CharSequence?, st: Int, b: Int, c: Int) {}
            })
        }
        val prefs = getSharedPreferences("settings", Context.MODE_PRIVATE)
        dimButton = Button(this).apply {
            setOnClickListener {
                prefs.edit().putBoolean("dim", !prefs.getBoolean("dim", false)).apply()
                applyDim(prefs.getBoolean("dim", false))
            }
        }
        applyDim(prefs.getBoolean("dim", false))
        val exportButton = Button(this).apply {
            text = "Export latest ride to .fit"
            setOnClickListener { exportFit() }
        }
        summary = TextView(this).apply { textSize = 13f; typeface = Typeface.MONOSPACE }
        logView = TextView(this).apply { textSize = 10f; typeface = Typeface.MONOSPACE }
        dynStatusView = TextView(this).apply { textSize = 12f }
        val dynSwitch = Switch(this).apply {
            text = "Ask pedals for cycling dynamics (8 Hz)"
            isChecked = prefs.getBoolean("dyn", true)
            setOnCheckedChangeListener { _, on ->
                prefs.edit().putBoolean("dyn", on).apply()
                RecorderService.session?.enableDynamics = on
            }
        }
        liveView = LiveView(this) { RecorderService.session }
        root.addView(status)
        root.addView(button)
        root.addView(recordButton)
        root.addView(wheelEdit)
        root.addView(dynSwitch)
        root.addView(dynStatusView)
        root.addView(liveView, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(summary)
        root.addView(logView)
        root.addView(exportButton)
        root.addView(dimButton)
        setContentView(ScrollView(this).apply { addView(root) })

        val filter = IntentFilter(ACTION_PERMISSION)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(permissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            }
        } else {
            registerReceiver(permissionReceiver, filter)
        }
        ui.post(tick)
    }

    override fun onDestroy() {
        // the recording service keeps running on purpose; only the UI goes away
        ui.removeCallbacks(tick)
        unregisterReceiver(permissionReceiver)
        super.onDestroy()
    }

    private fun onButton() {
        if (RecorderService.session != null) {
            stopService(Intent(this, RecorderService::class.java))
            status.text = "stopped"
            return
        }
        val device: UsbDevice? = UsbAnt.find(usbManager)
        if (device == null) {
            val all = usbManager.deviceList.values.joinToString { "%04X:%04X".format(it.vendorId, it.productId) }
            status.text = "No ANT stick found. USB devices seen: ${all.ifEmpty { "none" }}"
            return
        }
        if (usbManager.hasPermission(device)) {
            startRecording()
        } else {
            val intent = Intent(ACTION_PERMISSION).setPackage(packageName)
            val pi = PendingIntent.getBroadcast(this, 0, intent, PendingIntent.FLAG_MUTABLE)
            usbManager.requestPermission(device, pi)
            status.text = "requesting USB permission..."
        }
    }

    /** Setting "Dim screen while open": off by default, the screen never dims on its own. */
    private fun applyDim(dim: Boolean) {
        val lp = window.attributes
        lp.screenBrightness = if (dim) 0.05f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        window.attributes = lp
        dimButton.text = if (dim) "Dim screen: ON (tap to turn off)" else "Dim screen: OFF (tap to turn on)"
    }

    private fun exportFit() {
        val dir = getExternalFilesDir(null) ?: return
        val capture = dir.listFiles { f -> f.name.startsWith("capture_") && f.name.endsWith(".csv") }
            ?.maxByOrNull { it.name }
        if (capture == null) {
            status.text = "no capture to export"
            return
        }
        status.text = "exporting ${capture.name} ..."
        Thread {
            val msg = try {
                val samples = capture.useLines { CaptureReader.read(it) }
                val fit = File(dir, capture.name.removeSuffix(".csv").replace("capture_", "ride_") + ".fit")
                val r = FitExporter.export(samples, fit)
                if (r == null) {
                    "nothing to export: no decodable samples in ${capture.name}"
                } else {
                    val where = copyToDownloads(fit)
                    "exported ${fit.name}: ${r.seconds}s, avg HR ${r.avgHr ?: "-"}, max ${r.maxHr ?: "-"}. $where"
                }
            } catch (e: Exception) {
                "EXPORT ERROR: ${e.message}"
            }
            ui.post { status.text = msg }
        }.start()
    }

    /** Makes the .fit visible in the phone's Downloads folder (Android 10+), ready for upload. */
    private fun copyToDownloads(src: File): String {
        if (Build.VERSION.SDK_INT < 29) return "saved in app folder only"
        val values = android.content.ContentValues().apply {
            put(android.provider.MediaStore.Downloads.DISPLAY_NAME, src.name)
            put(android.provider.MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
            put(android.provider.MediaStore.Downloads.RELATIVE_PATH, "Download")
        }
        val uri = contentResolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: return "could not write to Downloads"
        contentResolver.openOutputStream(uri)?.use { o -> src.inputStream().use { it.copyTo(o) } }
        return "Copied to Downloads/${src.name}"
    }

    private fun startRecording() {
        startForegroundService(Intent(this, RecorderService::class.java))
        status.text = "starting..."
    }

    companion object {
        const val ACTION_PERMISSION = "app.antlogger.USB_PERMISSION"
    }
}
