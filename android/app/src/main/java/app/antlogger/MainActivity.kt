package app.antlogger

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.GestureDetector
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs

/**
 * Two pages, swipe or tap the tabs, no scrolling, everything sized from the display:
 *  - Setup: connect, start recording, heart rate and power, settings, raw message log.
 *  - Live: pedal dynamics and the Stop recording button.
 * A status strip on top of both pages shows the connection, recording time, heart rate, power and speed.
 */
class MainActivity : Activity() {
    private lateinit var usbManager: UsbManager
    private val ui = Handler(Looper.getMainLooper())

    private lateinit var strip: TextView
    private lateinit var setupPage: LinearLayout
    private lateinit var livePage: LinearLayout
    private lateinit var tabSetup: Button
    private lateinit var tabLive: Button
    private lateinit var connectButton: Button
    private lateinit var recordButton: Button
    private lateinit var garminButton: Button
    private lateinit var statusText: TextView
    private lateinit var pagesView: TextView
    private lateinit var devInfo: TextView
    private lateinit var logView: TextView
    private lateinit var liveView: LiveView
    private lateinit var gestures: GestureDetector

    private var page = 0
    private var wasRiding = false
    private var dimEnabled = false
    private var dimmed = false
    private var swallowTouch = false
    private var lastTouchMs = System.currentTimeMillis()
    private var note: String? = null
    private var noteUntil = 0L

    private val permissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != ACTION_PERMISSION) return
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            if (granted) startService() else say("USB permission denied")
        }
    }

    private fun say(msg: String, seconds: Int = 12) {
        note = msg
        noteUntil = System.currentTimeMillis() + seconds * 1000L
    }

    private fun lv(metric: String): Double? {
        val p = RecorderService.session?.live?.get(metric) ?: return null
        return if (System.currentTimeMillis() - p.second <= 4000) p.first else null
    }

    private val tick = object : Runnable {
        override fun run() {
            val s = RecorderService.session
            val riding = s?.rideActive == true
            // starting a ride jumps to the live page, stopping returns to setup
            if (riding && !wasRiding) show(1)
            if (!riding && wasRiding) {
                // the ride just ended (RIDE_STOP is written and flushed): export it automatically
                show(0)
                exportFit()
            }
            wasRiding = riding

            updateStrip(s)
            if (dimEnabled && !dimmed && System.currentTimeMillis() - lastTouchMs > DIM_AFTER_MS) {
                setBrightness(0.02f) // the lowest useful level; the app keeps the screen on
                dimmed = true
            }
            if (s != null) {
                connectButton.isEnabled = !riding
                connectButton.text = if (riding) "Disconnect (stop recording first)" else "Disconnect"
                // one button for both: start (needs a live heart rate) and stop (red while recording)
                recordButton.isEnabled = riding || s.heartRateLive()
                recordButton.text = when {
                    riding -> "STOP RECORDING"
                    s.heartRateLive() -> "Start recording"
                    else -> "Start recording (waiting for heart rate...)"
                }
                recordButton.setBackgroundColor(if (riding) Color.rgb(170, 30, 30) else Color.rgb(40, 40, 40))
                pagesView.text = s.pageCounts.entries.sortedBy { it.key }
                    .joinToString("   ") { "${it.key} x${it.value}" }.ifEmpty { "no broadcast pages yet" }
            } else {
                connectButton.isEnabled = true
                connectButton.text = "Connect"
                recordButton.isEnabled = false
                recordButton.text = "Start recording (connect first)"
                recordButton.setBackgroundColor(Color.rgb(40, 40, 40))
                pagesView.text = ""
            }
            devInfo.text = deviceInfo(s)
            statusText.text = if (System.currentTimeMillis() < noteUntil) note
            else if (s != null) "raw log ${RecorderService.logFile?.name}" else "idle"

            // show as many of the newest raw messages as fit the log area
            val lines = (logView.height / logView.lineHeight.coerceAtLeast(1)).coerceAtLeast(3)
            logView.text = RecorderService.lines.toList().takeLast(lines).joinToString("\n")

            liveView.invalidate()
            ui.postDelayed(this, 500)
        }
    }

    /** Battery and the dynamics settings the pedals report. Both arrive rarely, so they stay for 2 minutes. */
    private fun deviceInfo(s: AntSession?): String {
        if (s == null) return "Pedals: not connected"
        fun long(metric: String): Double? =
            s.live[metric]?.takeIf { System.currentTimeMillis() - it.second <= 120_000 }?.first
        val batt = long(Metrics.BATTERY_V)?.let { v ->
            val st = when (long(Metrics.BATTERY_STATUS)?.toInt()) {
                1 -> "new"; 2 -> "good"; 3 -> "ok"; 4 -> "low"; 5 -> "critical"; else -> "?"
            }
            "%.2f V (%s)".format(v, st)
        } ?: "-"
        val flags = listOf(
            Metrics.DYN_8HZ to "8 Hz", Metrics.DYN_POWER_PHASE to "power phase", Metrics.DYN_PCO to "PCO",
            Metrics.DYN_RIDER_POSITION to "rider position", Metrics.DYN_BARYCENTER to "barycenter",
        ).mapNotNull { (m, n) -> long(m)?.let { "$n ${if (it > 0.5) "on" else "off"}" } }
        return "Pedal battery: $batt\nPedals report: " + (if (flags.isEmpty()) "no settings broadcast yet" else flags.joinToString(", "))
    }

    private fun updateStrip(s: AntSession?) {
        val hr = lv(Metrics.HEART_RATE)?.let { "HR ${it.toInt()}" } ?: "HR -"
        val pw = lv(Metrics.POWER)?.let { "${it.toInt()} W" } ?: "- W"
        val sp = lv(Metrics.SPEED_KMH)?.let { "%.1f km/h".format(it) }
        val extra = listOfNotNull(hr, pw, sp).joinToString(" · ")
        when {
            s == null -> {
                strip.text = "NOT CONNECTED"
                strip.setBackgroundColor(Color.rgb(40, 40, 40))
                strip.setTextColor(Color.rgb(170, 170, 170))
            }
            s.rideActive -> {
                val sec = (System.currentTimeMillis() - s.rideStartMs) / 1000
                strip.text = "● REC %d:%02d:%02d · %s".format(sec / 3600, sec % 3600 / 60, sec % 60, extra)
                strip.setBackgroundColor(Color.rgb(120, 20, 20))
                strip.setTextColor(Color.WHITE)
            }
            else -> {
                strip.text = "CONNECTED · not recording · $extra"
                strip.setBackgroundColor(Color.rgb(20, 70, 40))
                strip.setTextColor(Color.WHITE)
            }
        }
    }

    private fun show(i: Int) {
        page = i
        setupPage.visibility = if (i == 0) View.VISIBLE else View.GONE
        livePage.visibility = if (i == 1) View.VISIBLE else View.GONE
        tabSetup.setBackgroundColor(if (i == 0) Color.rgb(70, 70, 70) else Color.rgb(25, 25, 25))
        tabLive.setBackgroundColor(if (i == 1) Color.rgb(70, 70, 70) else Color.rgb(25, 25, 25))
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        lastTouchMs = System.currentTimeMillis()
        if (dimmed) {
            // wake the screen; this first touch is swallowed so it cannot press a button on a dark screen
            setBrightness(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE)
            dimmed = false
            swallowTouch = true
            return true
        }
        if (swallowTouch) {
            if (ev.actionMasked == MotionEvent.ACTION_UP || ev.actionMasked == MotionEvent.ACTION_CANCEL) swallowTouch = false
            return true
        }
        gestures.onTouchEvent(ev) // swipe detection; the children still get the touch
        return super.dispatchTouchEvent(ev)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // true black everywhere: window, status bar and navigation bar (screen stays on while recording)
        window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(Color.BLACK))
        window.statusBarColor = Color.BLACK
        window.navigationBarColor = Color.BLACK

        val dm = resources.displayMetrics
        val h = dm.heightPixels.toFloat()
        val tStrip = h * 0.021f
        val tBtn = h * 0.024f
        val tSmall = h * 0.0165f
        fun Button.dark(size: Float = tBtn): Button {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, size)
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.rgb(40, 40, 40))
            isAllCaps = false
            return this
        }
        fun weight(w: Float, height: Int = 0) = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, height, w)
        fun gap(v: View) { (v.layoutParams as LinearLayout.LayoutParams).setMargins(0, (h * 0.004f).toInt(), 0, (h * 0.004f).toInt()) }

        val prefs = getSharedPreferences("settings", Context.MODE_PRIVATE)

        strip = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tStrip)
            gravity = Gravity.CENTER
            setPadding(0, (h * 0.008f).toInt(), 0, (h * 0.008f).toInt())
            typeface = Typeface.DEFAULT_BOLD
        }

        // ---------- setup page ----------
        connectButton = Button(this).dark().apply { text = "Connect"; setOnClickListener { onConnect() } }
        recordButton = Button(this).dark().apply {
            text = "Start recording (connect first)"
            isEnabled = false
            setOnClickListener {
                val s = RecorderService.session ?: return@setOnClickListener
                if (s.rideActive) s.endRide() else s.beginRide()
            }
        }
        garminButton = Button(this).dark(tBtn * 0.85f).apply {
            text = "Open Garmin import page (Chrome)"
            setOnClickListener { openGarminImportPage() }
        }
        // "Dim screen when idle": a checkbox, off by default; a touch brings the brightness back
        val dimCheck = android.widget.CheckBox(this).apply {
            text = "Dim screen when idle (touch wakes it)"
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tBtn * 0.8f)
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("dim", false)
            setOnCheckedChangeListener { _, on ->
                prefs.edit().putBoolean("dim", on).apply()
                applyDim(on)
            }
        }
        applyDim(dimCheck.isChecked)
        val wheelEdit = EditText(this).apply {
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tBtn * 0.8f)
            setTextColor(Color.WHITE)
            setText(prefs.getInt("wheel_mm", 2105).toString())
            gravity = Gravity.CENTER
            addTextChangedListener(object : android.text.TextWatcher {
                override fun afterTextChanged(s: android.text.Editable?) {
                    val mm = s?.toString()?.toIntOrNull() ?: return
                    if (mm in 1000..3000) {
                        prefs.edit().putInt("wheel_mm", mm).apply()
                        RecorderService.session?.wheelM = mm / 1000.0
                    }
                }
                override fun beforeTextChanged(s: CharSequence?, st: Int, c: Int, a: Int) {}
                override fun onTextChanged(s: CharSequence?, st: Int, b: Int, c: Int) {}
            })
        }
        val wheelRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(TextView(context).apply {
                text = "Wheel circumference (mm), for speed sensors"
                setTextSize(TypedValue.COMPLEX_UNIT_PX, tSmall)
                setTextColor(Color.rgb(170, 170, 170))
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f))
            addView(wheelEdit, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }
        statusText = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tSmall)
            setTextColor(Color.rgb(200, 200, 200))
            gravity = Gravity.CENTER_VERTICAL
        }
        devInfo = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tSmall)
            setTextColor(Color.rgb(200, 200, 200))
            gravity = Gravity.CENTER_VERTICAL
        }
        val readOnly = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tSmall * 0.9f)
            setTextColor(Color.rgb(130, 130, 130))
            text = "Read-only: this app only listens and never sends anything to your sensors."
            gravity = Gravity.CENTER_VERTICAL
        }
        pagesView = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tSmall * 0.85f)
            setTextColor(Color.rgb(150, 150, 150))
            typeface = Typeface.MONOSPACE
        }
        logView = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_PX, tSmall * 0.8f)
            setTextColor(Color.rgb(190, 190, 190))
            typeface = Typeface.MONOSPACE
            gravity = Gravity.BOTTOM
        }
        setupPage = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((dm.widthPixels * 0.02f).toInt(), 0, (dm.widthPixels * 0.02f).toInt(), 0)
            // controls first, right under the banner; heart rate and power are in the banner
            addView(connectButton, weight(1.0f)); gap(connectButton)
            addView(garminButton, weight(1.0f)); gap(garminButton)
            addView(dimCheck, weight(0.8f))
            addView(wheelRow, weight(0.8f))
            addView(statusText, weight(1.5f)) // room for the export / recovery message (3-4 lines)
            addView(devInfo, weight(1.2f))
            addView(readOnly, weight(0.7f))
            addView(pagesView, weight(1.3f))
            addView(logView, weight(3.4f))
        }

        // ---------- live page ----------
        liveView = LiveView(this) { RecorderService.session }
        livePage = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(recordButton, weight(1.0f)); gap(recordButton) // same place and size as Connect on Setup
            addView(liveView, weight(11f)) // the dashboard fills the rest of the page
        }

        // ---------- navigation ----------
        tabSetup = Button(this).dark().apply { text = "Setup"; setOnClickListener { show(0) } }
        tabLive = Button(this).dark().apply { text = "Live"; setOnClickListener { show(1) } }
        val nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(tabSetup, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f).apply { setMargins(0, 0, (h * 0.003f).toInt(), 0) })
            addView(tabLive, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f))
        }
        val pages = FrameLayout(this).apply {
            addView(setupPage, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            addView(livePage, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
            addView(strip, weight(0f, ViewGroup.LayoutParams.WRAP_CONTENT))
            addView(pages, weight(1f))
            addView(nav, weight(0f, (h * 0.075f).toInt()))
            // keep clear of the system bars (the app draws edge to edge on newer Android)
            setOnApplyWindowInsetsListener { v, ins ->
                if (Build.VERSION.SDK_INT >= 30) {
                    val b = ins.getInsets(WindowInsets.Type.systemBars())
                    v.setPadding(b.left, b.top, b.right, b.bottom)
                } else {
                    @Suppress("DEPRECATION")
                    v.setPadding(ins.systemWindowInsetLeft, ins.systemWindowInsetTop, ins.systemWindowInsetRight, ins.systemWindowInsetBottom)
                }
                ins
            }
        }
        setContentView(root)
        show(0)

        // swipe left/right to change page
        gestures = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onFling(e1: MotionEvent?, e2: MotionEvent, vx: Float, vy: Float): Boolean {
                if (e1 == null) return false
                val dx = e2.x - e1.x
                val dy = e2.y - e1.y
                if (abs(dx) > dm.widthPixels * 0.25f && abs(dx) > abs(dy) * 1.5f) {
                    show(if (dx < 0) 1 else 0)
                    return true
                }
                return false
            }
        })

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
        recoverUnfinishedRides()
    }

    override fun onDestroy() {
        // the recording service keeps running on purpose; only the UI goes away
        ui.removeCallbacks(tick)
        unregisterReceiver(permissionReceiver)
        super.onDestroy()
    }

    private fun onConnect() {
        if (RecorderService.session != null) {
            stopService(Intent(this, RecorderService::class.java))
            say("disconnected")
            return
        }
        val device: UsbDevice? = UsbAnt.find(usbManager)
        if (device == null) {
            val all = usbManager.deviceList.values.joinToString { "%04X:%04X".format(it.vendorId, it.productId) }
            say("No ANT stick found. USB devices seen: ${all.ifEmpty { "none" }}")
            return
        }
        if (usbManager.hasPermission(device)) {
            startService()
        } else {
            val intent = Intent(ACTION_PERMISSION).setPackage(packageName)
            val pi = PendingIntent.getBroadcast(this, 0, intent, PendingIntent.FLAG_MUTABLE)
            usbManager.requestPermission(device, pi)
            say("requesting USB permission...")
        }
    }

    private fun setBrightness(level: Float) {
        val lp = window.attributes
        lp.screenBrightness = level
        window.attributes = lp
    }

    /**
     * Setting "Dim screen when idle": off by default. When on, the screen drops to a very low brightness after
     * [DIM_AFTER_MS] without a touch; any touch brings the normal brightness back at once. The screen itself
     * never turns off (the app holds it on).
     */
    private fun applyDim(on: Boolean) {
        dimEnabled = on
        lastTouchMs = System.currentTimeMillis()
        if (!on && dimmed) {
            setBrightness(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE)
            dimmed = false
        }
    }

    /** A ride's .fit is named after its start time, so several rides in one capture never overwrite each other. */
    private fun fitFileFor(dir: File, startMs: Long) =
        File(dir, "ride_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date(startMs)) + ".fit")

    /** Exports the latest ride of [capture] and returns a one-line result. Call from a background thread. */
    private fun exportCapture(capture: File, dir: File, recovered: Boolean): String {
        return try {
            val lines = capture.readLines()
            val info = CaptureReader.rideInfo(lines.asSequence())
            val samples = CaptureReader.read(lines.asSequence())
            val startMs = info.startMs ?: samples.firstOrNull()?.tsMs
                ?: return "nothing to export in ${capture.name}"
            val fit = fitFileFor(dir, startMs)
            val r = FitExporter.export(samples, fit)
                ?: return "nothing to export: no decodable samples in ${capture.name}"
            val where = copyToDownloads(fit)
            (if (recovered) "RECOVERED unfinished ride: " else "exported ") +
                "${fit.name}: ${r.seconds}s, avg HR ${r.avgHr ?: "-"}, max ${r.maxHr ?: "-"}. $where. " +
                "To upload: tap the Garmin button and choose it."
        } catch (e: Exception) {
            "EXPORT ERROR: ${e.message}"
        }
    }

    /** Called when a ride is stopped: exports the ride of the newest capture automatically. */
    private fun exportFit() {
        val dir = getExternalFilesDir(null) ?: return
        val capture = dir.listFiles { f -> f.name.startsWith("capture_") && f.name.endsWith(".csv") }
            ?.maxByOrNull { it.name }
        if (capture == null) {
            say("no capture to export")
            return
        }
        say("exporting ${capture.name} ...")
        Thread {
            val msg = exportCapture(capture, dir, recovered = false)
            ui.post { say(msg, 30) }
        }.start()
    }

    /**
     * At app start, before any connection: a ride that began but never got a stop marker (app killed, stick
     * unplugged, phone died) is exported from its raw log. Each capture is looked at once; a ride whose .fit
     * already exists is skipped. Nothing happens while a recording service is running, because it owns its file.
     */
    private fun recoverUnfinishedRides() {
        if (RecorderService.session != null) return
        val dir = getExternalFilesDir(null) ?: return
        val prefs = getSharedPreferences("settings", Context.MODE_PRIVATE)
        val scanned = prefs.getStringSet("scanned_captures", emptySet())!!.toMutableSet()
        val captures = dir.listFiles { f -> f.name.startsWith("capture_") && f.name.endsWith(".csv") }
            ?.sortedBy { it.name }?.filter { it.name !in scanned } ?: return
        if (captures.isEmpty()) return
        Thread {
            val results = ArrayList<String>()
            for (cap in captures) {
                try {
                    val info = cap.useLines { CaptureReader.rideInfo(it) }
                    if (info.startMs != null && !info.finished && !fitFileFor(dir, info.startMs).exists()) {
                        results += exportCapture(cap, dir, recovered = true)
                    }
                    scanned += cap.name
                } catch (e: Exception) {
                    results += "could not check ${cap.name}: ${e.message}"
                }
            }
            prefs.edit().putStringSet("scanned_captures", scanned).apply()
            if (results.isNotEmpty()) ui.post { say(results.joinToString("\n"), 90) }
        }.start()
    }

    /**
     * Opens Garmin Connect's import page in Chrome (the Garmin Connect app itself cannot import activity files: it
     * treats a .fit as a course). The user then picks the exported ride from Downloads. A plain link: no password,
     * no internet access in this app. Chrome is named explicitly so Garmin's app does not claim the link; without
     * Chrome the default browser is used.
     */
    private fun openGarminImportPage() {
        val uri = android.net.Uri.parse(GARMIN_IMPORT_URL)
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri).setPackage("com.android.chrome"))
        } catch (e: Exception) {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (e2: Exception) {
                say("No browser could open the Garmin import page: ${e2.message}", 30)
            }
        }
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

    private fun startService() {
        startForegroundService(Intent(this, RecorderService::class.java))
        say("connecting...")
    }

    companion object {
        const val ACTION_PERMISSION = "app.antlogger.USB_PERMISSION"
        const val DIM_AFTER_MS = 15_000L
        const val GARMIN_IMPORT_URL = "https://connect.garmin.com/app/import-data"
    }
}
