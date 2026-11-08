package app.sensorsim

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
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView

/**
 * One screen. Source: the WaterRower S4 over USB, or manual sliders for testing. Output: a Bluetooth rower for MyWhoosh.
 * The rower is never asked to type anything while rowing: the only setting is the monitor's fill level, set beforehand.
 */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var sent: TextView
    private lateinit var startBtn: Button
    private lateinit var pauseBtn: Button
    private lateinit var rowerBox: CheckBox
    private lateinit var powerBox: CheckBox
    private var pendingStart: (() -> Unit)? = null

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun text(s: String, sp: Float, bold: Boolean = false) = TextView(this).apply {
        text = s
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(Color.WHITE)
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    /** A labelled slider; [fmt] turns the integer position into the shown value, [onChange] stores it. */
    private fun slider(parent: LinearLayout, title: String, max: Int, start: Int, fmt: (Int) -> String, onChange: (Int) -> Unit) {
        val label = text("", 18f)
        parent.addView(text(title, 14f).apply { setTextColor(Color.LTGRAY); setPadding(0, dp(16), 0, 0) })
        parent.addView(label)
        val bar = SeekBar(this).apply {
            this.max = max
            progress = start
            setPadding(dp(8), dp(8), dp(8), dp(8))
        }
        label.text = fmt(start)
        bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar, p: Int, fromUser: Boolean) { label.text = fmt(p); onChange(p) }
            override fun onStartTrackingTouch(s: SeekBar) {}
            override fun onStopTrackingTouch(s: SeekBar) {}
        })
        parent.addView(bar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)))
    }

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != ACTION_USB) return
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            val go = pendingStart
            pendingStart = null
            if (granted) go?.invoke() else { Hub.status = "USB permission denied"; refresh() }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("settings", MODE_PRIVATE)
        Hub.useRower = prefs.getBoolean("useRower", Hub.useRower)
        Hub.fillLitres = prefs.getFloat("fill", Hub.fillLitres.toFloat()).toDouble()

        val filter = IntentFilter(ACTION_USB)
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(usbReceiver, filter, RECEIVER_NOT_EXPORTED)
        else registerReceiver(usbReceiver, filter)

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
            setBackgroundColor(Color.rgb(18, 18, 22))
        }
        col.addView(text("BLE Sensor Sim", 26f, true))
        col.addView(text("Poses as a Bluetooth rower for MyWhoosh. Source: the WaterRower S4 on the USB OTG port.", 13f)
            .apply { setTextColor(Color.LTGRAY) })

        rowerBox = CheckBox(this).apply {
            text = "Use the USB rower (WaterRower S4)"
            isChecked = Hub.useRower
            setTextColor(Color.WHITE)
            setOnCheckedChangeListener { _, v -> Hub.useRower = v; prefs.edit().putBoolean("useRower", v).apply() }
        }
        col.addView(rowerBox, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(16) })

        powerBox = CheckBox(this).apply {
            text = "Also send power as a Cycling Power sensor (MyWhoosh power)"
            isChecked = Hub.cps
            setTextColor(Color.WHITE)
            setOnCheckedChangeListener { _, v -> Hub.cps = v }
        }
        col.addView(powerBox, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        slider(col, "Monitor fill setting (only scales the watts; set it to what the monitor shows)", 14,
            ((Hub.fillLitres - 13.0) * 2).toInt().coerceIn(0, 14), { "%.1f L".format(13.0 + it / 2.0) }) {
            Hub.fillLitres = 13.0 + it / 2.0
            prefs.edit().putFloat("fill", Hub.fillLitres.toFloat()).apply()
        }

        col.addView(text("Manual values (used when the USB rower is off)", 14f)
            .apply { setTextColor(Color.LTGRAY); setPadding(0, dp(24), 0, 0) })
        slider(col, "Power (the pace follows from it, as on the real rower)", 600, Hub.powerW, {
            // speed from power with the cube law and the fill setting, shown as the pace MyWhoosh will display
            val v = if (it > 0) Math.cbrt(it / (S4.WATT_CONST * S4.fillFactor(Hub.fillLitres))) else 0.0
            val pace = Ble.paceS500(v)
            "$it W  =  ${if (pace == 0) "--" else "%d:%02d".format(pace / 60, pace % 60)} /500 m".format()
        }) { Hub.powerW = it }
        slider(col, "Stroke rate", 120, (Hub.strokeRate * 2).toInt(), { "%.1f spm".format(it / 2.0) }) { Hub.strokeRate = it / 2.0 }

        startBtn = Button(this).apply { setOnClickListener { toggle() } }
        pauseBtn = Button(this).apply { setOnClickListener { Hub.paused = !Hub.paused; refresh() } }
        col.addView(startBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)).apply { topMargin = dp(16) })
        col.addView(pauseBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        status = text("", 16f, true).apply { setPadding(0, dp(16), 0, 0); gravity = Gravity.START }
        sent = text("", 13f).apply { setTextColor(Color.LTGRAY) }
        col.addView(status)
        col.addView(sent)
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.rgb(18, 18, 22)); addView(col) })
    }

    override fun onDestroy() {
        try { unregisterReceiver(usbReceiver) } catch (_: Exception) { }
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        ui.post(poll)
    }

    override fun onPause() {
        ui.removeCallbacks(poll)
        super.onPause()
    }

    private val poll = object : Runnable {
        override fun run() { refresh(); ui.postDelayed(this, 500) }
    }

    private fun refresh() {
        startBtn.text = if (Hub.running) "Stop" else "Start posing as a rower"
        pauseBtn.text = if (Hub.paused) "Resume (sending zeros now)" else "Pause (send zeros)"
        pauseBtn.isEnabled = !Hub.useRower
        rowerBox.isEnabled = !Hub.running
        powerBox.isEnabled = !Hub.running
        status.text = Hub.status
        sent.text = if (Hub.running) "Sending: ${Hub.sent}" else ""
    }

    private fun toggle() {
        if (Hub.running) {
            startService(Intent(this, SensorService::class.java).setAction(SensorService.ACTION_STOP))
            return
        }
        val need = ArrayList<String>()
        if (Build.VERSION.SDK_INT >= 31) {
            need += Manifest.permission.BLUETOOTH_ADVERTISE
            need += Manifest.permission.BLUETOOTH_CONNECT
        }
        if (Build.VERSION.SDK_INT >= 33) need += Manifest.permission.POST_NOTIFICATIONS
        val missing = need.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) { requestPermissions(missing.toTypedArray(), 1); return }
        if (Hub.useRower) ensureUsb { startSim() } else startSim()
    }

    private fun startSim() {
        Hub.status = "Starting"
        startForegroundService(Intent(this, SensorService::class.java))
        refresh()
    }

    /** The USB monitor needs the user's permission once (the system dialog); then [then] runs. */
    private fun ensureUsb(then: () -> Unit) {
        val mgr = getSystemService(UsbManager::class.java)
        val dev = UsbS4.find(mgr)
        if (dev == null) { Hub.status = "USB rower: monitor not found, plug it in with the OTG adapter"; refresh(); return }
        if (mgr.hasPermission(dev)) { then(); return }
        pendingStart = then
        val pi = PendingIntent.getBroadcast(this, 0, Intent(ACTION_USB).setPackage(packageName), PendingIntent.FLAG_MUTABLE)
        mgr.requestPermission(dev, pi)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (grantResults.all { it == PackageManager.PERMISSION_GRANTED }) toggle()
        else { Hub.status = "Bluetooth permission denied"; refresh() }
    }

    companion object {
        const val ACTION_USB = "app.sensorsim.USB_PERMISSION"
    }
}
