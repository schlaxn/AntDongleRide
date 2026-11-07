package app.antlogger

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import kotlin.math.abs

/**
 * Real-time pedal dashboard: power, cadence, left/right balance, torque effectiveness, pedal smoothness,
 * power phase (one crank diagram per pedal), platform center offset, rider position, torque barycenter.
 * Values older than [STALE_MS] show as "-". Reads AntSession.live; call invalidate() to refresh.
 */
class LiveView(context: Context, private val session: () -> AntSession?) : View(context) {
    private val d = resources.displayMetrics.density
    private val fg = Color.rgb(30, 30, 30)
    private val dim = Color.rgb(140, 140, 140)
    private val left = Color.rgb(0, 120, 200)
    private val right = Color.rgb(230, 110, 0)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = fg }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

    private fun v(metric: String): Double? {
        val p = session()?.live?.get(metric) ?: return null
        return if (System.currentTimeMillis() - p.second <= STALE_MS) p.first else null
    }

    private fun label(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int = fg, align: Paint.Align = Paint.Align.LEFT) {
        text.textSize = size * d
        text.color = color
        text.textAlign = align
        c.drawText(s, x, y, text)
    }

    private fun fmt(x: Double?, digits: Int = 0, unit: String = "") =
        if (x == null) "-" else "%.${digits}f$unit".format(x)

    override fun onMeasure(w: Int, h: Int) {
        setMeasuredDimension(MeasureSpec.getSize(w), (930 * d).toInt())
    }

    /** A 0-100 % bar with the value printed at its right end. */
    private fun bar(c: Canvas, name: String, value: Double?, color: Int, x: Float, y: Float, w: Float) {
        label(c, name, x, y, 12f, dim)
        val top = y + 6 * d
        val r = RectF(x, top, x + w, top + 14 * d)
        fill.color = Color.rgb(230, 230, 230)
        c.drawRoundRect(r, 4 * d, 4 * d, fill)
        if (value != null) {
            fill.color = color
            c.drawRoundRect(RectF(x, top, x + w * (value / 100.0).coerceIn(0.0, 1.0).toFloat(), top + 14 * d), 4 * d, 4 * d, fill)
        }
        label(c, fmt(value, 1, " %"), x + w, y, 12f, fg, Paint.Align.RIGHT)
    }

    /** Crank diagram: 0 deg at the top, clockwise. Draws the positive-torque window and the peak window. */
    private fun crank(c: Canvas, title: String, side: String, color: Int, cx: Float, cy: Float, r: Float) {
        stroke.strokeWidth = 1.5f * d
        stroke.color = Color.rgb(200, 200, 200)
        c.drawCircle(cx, cy, r, stroke)
        label(c, "0", cx, cy - r - 4 * d, 10f, dim, Paint.Align.CENTER)
        label(c, "180", cx, cy + r + 14 * d, 10f, dim, Paint.Align.CENTER)
        val s = v(Metrics.fa(side, "start_deg"))
        val e = v(Metrics.fa(side, "end_deg"))
        val ps = v(Metrics.fa(side, "peak_start_deg"))
        val pe = v(Metrics.fa(side, "peak_end_deg"))
        val oval = RectF(cx - r, cy - r, cx + r, cy + r)
        if (s != null && e != null) {
            var sweep = (e - s) % 360.0
            if (sweep <= 0) sweep += 360.0
            stroke.color = color
            stroke.alpha = 110
            stroke.strokeWidth = 12 * d
            c.drawArc(oval, (s - 90).toFloat(), sweep.toFloat(), false, stroke)
            stroke.alpha = 255
        }
        if (ps != null && pe != null) {
            var sweep = (pe - ps) % 360.0
            if (sweep <= 0) sweep += 360.0
            stroke.color = color
            stroke.strokeWidth = 12 * d
            c.drawArc(oval, (ps - 90).toFloat(), sweep.toFloat(), false, stroke)
        }
        label(c, title, cx, cy - 2 * d, 14f, color, Paint.Align.CENTER)
        label(c, fmt(v(Metrics.fa(side, "torque_nm")), 1, " Nm"), cx, cy + 14 * d, 11f, fg, Paint.Align.CENTER)
        val range = if (s != null && e != null) "${fmt(s)}-${fmt(e)}°" else "-"
        val peak = if (ps != null && pe != null) "peak ${fmt(ps)}-${fmt(pe)}°" else "peak -"
        label(c, range, cx, cy + r + 30 * d, 11f, fg, Paint.Align.CENTER)
        label(c, peak, cx, cy + r + 44 * d, 11f, dim, Paint.Align.CENTER)
    }

    /** Platform center offset scale, +-[range] mm, + = outward from the bike. */
    private fun pco(c: Canvas, name: String, value: Double?, color: Int, x: Float, y: Float, w: Float, range: Double = 20.0) {
        label(c, name, x, y, 12f, dim)
        label(c, fmt(value, 0, " mm"), x + w, y, 12f, fg, Paint.Align.RIGHT)
        val mid = x + w / 2
        val ly = y + 14 * d
        stroke.color = Color.rgb(200, 200, 200)
        stroke.strokeWidth = 2 * d
        c.drawLine(x, ly, x + w, ly, stroke)
        c.drawLine(mid, ly - 6 * d, mid, ly + 6 * d, stroke)
        if (value != null) {
            val px = mid + (value / range).coerceIn(-1.0, 1.0).toFloat() * (w / 2)
            fill.color = color
            c.drawCircle(px, ly, 7 * d, fill)
        }
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat()
        val pad = 12 * d
        var y = 34 * d

        // power and cadence
        label(c, fmt(v(Metrics.POWER)), pad, y + 24 * d, 56f)
        label(c, "W", pad + 118 * d, y + 24 * d, 16f, dim)
        label(c, fmt(v(Metrics.CADENCE)), w / 2 + 20 * d, y + 24 * d, 56f)
        label(c, "rpm", w / 2 + 130 * d, y + 24 * d, 16f, dim)
        y += 62 * d

        // balance
        val r = v(Metrics.BALANCE_RIGHT)
        label(c, "Balance L / R", pad, y, 12f, dim)
        val by = y + 8 * d
        val bw = w - 2 * pad
        fill.color = Color.rgb(230, 230, 230)
        c.drawRoundRect(RectF(pad, by, pad + bw, by + 26 * d), 6 * d, 6 * d, fill)
        if (r != null) {
            val lw = bw * ((100 - r) / 100).toFloat()
            fill.color = left
            c.drawRoundRect(RectF(pad, by, pad + lw, by + 26 * d), 6 * d, 6 * d, fill)
            fill.color = right
            c.drawRoundRect(RectF(pad + lw, by, pad + bw, by + 26 * d), 6 * d, 6 * d, fill)
            label(c, "L ${fmt(100 - r, 0, " %")}", pad + 8 * d, by + 19 * d, 14f, Color.WHITE)
            label(c, "R ${fmt(r, 0, " %")}", pad + bw - 8 * d, by + 19 * d, 14f, Color.WHITE, Paint.Align.RIGHT)
        } else {
            label(c, "-", pad + bw / 2, by + 19 * d, 14f, dim, Paint.Align.CENTER)
        }
        y = by + 52 * d

        // torque effectiveness and pedal smoothness
        val half = (bw - 16 * d) / 2
        bar(c, "Torque eff. L", v(Metrics.TE_LEFT), left, pad, y, half)
        bar(c, "Torque eff. R", v(Metrics.TE_RIGHT), right, pad + half + 16 * d, y, half)
        y += 40 * d
        val comb = v(Metrics.PS_COMBINED)
        if (comb != null && v(Metrics.PS_LEFT) == null && v(Metrics.PS_RIGHT) == null) {
            bar(c, "Pedal smoothness (combined)", comb, fg, pad, y, bw)
        } else {
            bar(c, "Pedal smooth. L", v(Metrics.PS_LEFT), left, pad, y, half)
            bar(c, "Pedal smooth. R", v(Metrics.PS_RIGHT), right, pad + half + 16 * d, y, half)
        }
        y += 44 * d

        // power phase
        label(c, "Power phase (solid = peak torque window)", pad, y, 12f, dim)
        val cr = minOf(64 * d, (w / 4) - 24 * d)
        val cy = y + 26 * d + cr
        crank(c, "L", "left", left, w * 0.27f, cy, cr)
        crank(c, "R", "right", right, w * 0.73f, cy, cr)
        y = cy + cr + 82 * d

        // platform center offset
        pco(c, "PCO left", v(Metrics.PCO_LEFT), left, pad, y, half)
        pco(c, "PCO right", v(Metrics.PCO_RIGHT), right, pad + half + 16 * d, y, half)
        y += 44 * d

        // rider position, barycenter, battery
        val pos = v(Metrics.RIDER_POSITION)?.toInt()
        val posText = when (pos) {
            0 -> "seated"; 1 -> "to seated"; 2 -> "standing"; 3 -> "to standing"; else -> "-"
        }
        label(c, "Position: $posText", pad, y, 14f)
        label(c, "Barycenter: ${fmt(v(Metrics.BARYCENTER), 1, "°")}", w / 2, y, 14f)
        y += 22 * d
        val bv = v(Metrics.BATTERY_V)
        val bs = when (v(Metrics.BATTERY_STATUS)?.toInt()) {
            1 -> "new"; 2 -> "good"; 3 -> "ok"; 4 -> "low"; 5 -> "critical"; else -> "-"
        }
        label(c, "Pedal battery: ${fmt(bv, 2, " V")} ($bs)", pad, y, 14f)
        y += 40 * d

        // other ANT+ sensors: heart rate (required to record), speed and cadence (optional)
        label(c, "Other sensors", pad, y, 12f, dim)
        y += 34 * d
        val hr = v(Metrics.HEART_RATE)
        label(c, fmt(hr), pad, y + 10 * d, 44f, if (hr == null) Color.rgb(200, 40, 40) else fg)
        label(c, "bpm", pad + 100 * d, y + 10 * d, 14f, dim)
        label(c, "RR ${fmt(v(Metrics.RR_MS), 0, " ms")}", pad, y + 32 * d, 12f, dim)
        label(c, fmt(v(Metrics.SPEED_KMH), 1), w / 2, y + 10 * d, 44f)
        label(c, "km/h", w / 2 + 112 * d, y + 10 * d, 14f, dim)
        y += 62 * d
        label(c, "Sensor cadence: ${fmt(v(Metrics.CSC_CADENCE), 0, " rpm")}", pad, y, 14f)
        label(c, "Distance: ${fmt(v(Metrics.DISTANCE_M)?.div(1000.0), 2, " km")}", w / 2, y, 14f)
    }

    companion object {
        const val STALE_MS = 4000L
    }
}
