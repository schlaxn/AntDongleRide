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
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.Spinner
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
    private val restartBoxes = ArrayList<CheckBox>()   // choices that only take effect after a restart
    private var refreshSpeedLabel: () -> Unit = {}     // redraws the speed label (its watts depend on the fill setting)
    private lateinit var paceBox: LinearLayout         // manual mode: the pace slider
    private lateinit var factorBox: LinearLayout       // USB mode: the speed factor slider
    private lateinit var cadenceBox: LinearLayout      // bike: the cadence factor slider
    private lateinit var rowerFieldsBox: LinearLayout  // rower: the optional Rower Data fields
    private lateinit var profileSpinner: Spinner       // the profile: rower or bike
    private lateinit var strokeBox: LinearLayout       // manual stroke rate: hidden when the USB rower measures it

    /** Shows the controls that belong to the chosen profile and source. */
    private fun updateModeViews() {
        paceBox.visibility = if (Hub.useRower) View.GONE else View.VISIBLE
        factorBox.visibility = View.VISIBLE   // the speed factor applies to every source and profile
        cadenceBox.visibility = if (Hub.bike) View.VISIBLE else View.GONE
        strokeBox.visibility = if (Hub.useRower) View.GONE else View.VISIBLE
        rowerFieldsBox.visibility = if (Hub.bike) View.GONE else View.VISIBLE
    }
    private var pendingStart: (() -> Unit)? = null

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun text(s: String, sp: Float, bold: Boolean = false) = TextView(this).apply {
        text = s
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(Color.WHITE)
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    /** A labelled slider; [fmt] turns the integer position into the shown value, [onChange] stores it. */
    private fun slider(parent: LinearLayout, title: String, max: Int, start: Int, fmt: (Int) -> String, onChange: (Int) -> Unit): () -> Unit {
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
        return { label.text = fmt(bar.progress) }   // redraws the label, e.g. when another setting changed its meaning
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
        Hub.useRower = !prefs.getBoolean("simulate", false)   // default off: the real rower is used
        Hub.bike = prefs.getBoolean("bike", false)
        Hub.fillLitres = prefs.getFloat("fill", Hub.fillLitres.toFloat()).toDouble()
        // a saved choice may still hold average stroke rate or pace from the testing build: those break MyWhoosh
        Hub.fields = prefs.getInt("fields", 0) and (RowerFields.AVG_POWER or RowerFields.ENERGY or RowerFields.EXTRA_FEATURE)

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

        // the profile: the rower (default) or the bike (power and cadence). Locked while running.
        profileSpinner = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_item,
                listOf("Rower (FTMS, for MyWhoosh rowing)", "Bike (power and cadence, Cycling Power)")).also {
                it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            }
            setSelection(if (Hub.bike) 1 else 0, false)
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                    val bike = pos == 1
                    if (bike == Hub.bike) return
                    Hub.bike = bike
                    prefs.edit().putBoolean("bike", bike).apply()
                    updateModeViews()
                }
                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
        col.addView(profileSpinner, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(16) })

        // simulate the USB rower: off (default) uses the real WaterRower; on uses the sliders below instead
        rowerBox = CheckBox(this).apply {
            text = "Simulate the USB rower (use the sliders instead of the WaterRower S4)"
            isChecked = !Hub.useRower
            setTextColor(Color.WHITE)
            setOnCheckedChangeListener { _, v ->
                Hub.useRower = !v
                prefs.edit().putBoolean("simulate", v).apply()
                updateModeViews()
            }
        }

        rowerFieldsBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(rowerFieldsBox)
        rowerFieldsBox.addView(text("Rower data fields. The base fields always go out. To find what MyWhoosh accepts, tick ONE extra at a time:", 14f)
            .apply { setTextColor(Color.LTGRAY); setPadding(0, dp(16), 0, 0) })
        fun fieldBox(title: String, bit: Int, needsRestart: Boolean = false) {
            val cb = CheckBox(this).apply {
                text = title
                isChecked = Hub.fields and bit != 0
                setTextColor(Color.WHITE)
                setOnCheckedChangeListener { _, v ->
                    Hub.fields = if (v) Hub.fields or bit else Hub.fields and bit.inv()
                    prefs.edit().putInt("fields", Hub.fields).apply()
                }
            }
            if (needsRestart) restartBoxes.add(cb)
            rowerFieldsBox.addView(cb)
        }
        // average stroke rate and average pace are not offered: MyWhoosh misreads the packet when they are present
        fieldBox("Average power", RowerFields.AVG_POWER)
        fieldBox("Energy (an estimate)", RowerFields.ENERGY)
        fieldBox("Also announce cadence and energy in the feature list (needs a restart)", RowerFields.EXTRA_FEATURE, true)

        slider(col, "Monitor fill setting (only scales the watts; set it to what the monitor shows)", 14,
            ((Hub.fillLitres - 13.0) * 2).toInt().coerceIn(0, 14), { "%.1f L".format(13.0 + it / 2.0) }) {
            Hub.fillLitres = 13.0 + it / 2.0
            prefs.edit().putFloat("fill", Hub.fillLitres.toFloat()).apply()
            refreshSpeedLabel()   // the watts shown for the speed depend on the fill setting
        }

        paceBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        factorBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        cadenceBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(cadenceBox)
        col.addView(factorBox)
        // the simulate checkbox only changes the speed and stroke sliders below it, so it sits between the two groups
        col.addView(rowerBox, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(16) })
        col.addView(text("Speed and stroke (the simulated values; with the real rower, the speed factor scales the measured speed)", 14f)
            .apply { setTextColor(Color.LTGRAY); setPadding(0, dp(24), 0, 0) })
        col.addView(paceBox)
        slider(cadenceBox, "Cadence factor for the bike (2 to 4): cadence = stroke rate x this", 200,
            ((Hub.cadenceFactor - 2.0) * 100).toInt().coerceIn(0, 200), {
                val f = 2.0 + it / 100.0
                "x%.2f  (a stroke rate of 30 spm gives %.0f rpm)".format(f, 30 * f)
            }) { Hub.cadenceFactor = 2.0 + it / 100.0 }
        // logarithmic: 1/8 at the left, 1 in the middle, 8 at the right; each step is the same ratio
        slider(factorBox, "Speed factor (1/8 to 8): multiplies the measured speed (USB rower) or the manual pace; the watts follow it",
            600, (Math.round((Math.log(Hub.paceFactor) / Math.log(2.0) + 3.0) * 100)).toInt().coerceIn(0, 600), {
                "x%.3f  (the watts change with its cube)".format(Math.pow(2.0, it / 100.0 - 3.0))
            }) { Hub.paceFactor = Math.pow(2.0, it / 100.0 - 3.0) }
        // pace from 3:00 (slow, left) to 1:30 (fast, right) in one-second steps; speed and power follow from it
        refreshSpeedLabel = slider(paceBox, "Pace per 500 m (speed and power follow from it, as on the real rower)", 90,
            180 - Hub.paceS, {
                val pace = 180 - it
                val v = 500.0 / pace
                val watts = (S4.WATT_CONST * Math.pow(v, 3.0) * S4.fillFactor(Hub.fillLitres)).toInt()
                "%d:%02d /500 m  =  %.2f m/s  =  %d W".format(pace / 60, pace % 60, v, watts)
            }) { Hub.paceS = 180 - it }
        strokeBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(strokeBox)
        slider(strokeBox, "Stroke rate (only when simulating; the real rower measures it)", 120,
            (Hub.strokeRate * 2).toInt(), { "%.1f spm".format(it / 2.0) }) { Hub.strokeRate = it / 2.0 }
        updateModeViews()   // every box exists now: apply the visibility for the saved profile and source

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
        startBtn.text = if (Hub.running) "Stop" else "Start rowing"
        pauseBtn.text = if (Hub.paused) "Resume (sending zeros now)" else "Pause (send zeros)"
        pauseBtn.isEnabled = !Hub.useRower
        rowerBox.isEnabled = !Hub.running
        restartBoxes.forEach { it.isEnabled = !Hub.running }
        profileSpinner.isEnabled = !Hub.running
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
