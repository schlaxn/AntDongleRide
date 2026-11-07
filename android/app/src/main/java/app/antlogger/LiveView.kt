package app.antlogger

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF

/**
 * Live page: power, cadence, left/right balance and per-pedal watts, torque effectiveness, pedal
 * smoothness, power phase (one crank diagram per pedal), platform center offset and rider position.
 * Only what matters while riding; battery and the pedals' own settings are on the Setup page.
 * Laid out in fractions of the view size with large text.
 */
class LiveView(context: Context, session: () -> AntSession?) : DashView(context, session) {

    /** Crank diagram: 0 deg at the top, clockwise. Positive-torque window (faded) and peak window (solid). */
    private fun crank(c: Canvas, title: String, side: String, color: Int, cx: Float, cy: Float, r: Float) {
        val ring = r * 0.2f
        stroke.strokeWidth = ring * 0.12f
        stroke.color = outline
        c.drawCircle(cx, cy, r, stroke)
        val s = v(Metrics.fa(side, "start_deg"))
        val e = v(Metrics.fa(side, "end_deg"))
        val ps = v(Metrics.fa(side, "peak_start_deg"))
        val pe = v(Metrics.fa(side, "peak_end_deg"))
        val oval = RectF(cx - r, cy - r, cx + r, cy + r)
        stroke.strokeWidth = ring
        if (s != null && e != null) {
            var sweep = (e - s) % 360.0
            if (sweep <= 0) sweep += 360.0
            stroke.color = color
            stroke.alpha = 110
            c.drawArc(oval, (s - 90).toFloat(), sweep.toFloat(), false, stroke)
            stroke.alpha = 255
        }
        if (ps != null && pe != null) {
            var sweep = (pe - ps) % 360.0
            if (sweep <= 0) sweep += 360.0
            stroke.color = color
            c.drawArc(oval, (ps - 90).toFloat(), sweep.toFloat(), false, stroke)
        }
        label(c, title, cx, cy - sSmall() * 0.1f, sMed(), color, Paint.Align.CENTER)
        label(c, fmt(v(Metrics.fa(side, "torque_nm")), 1, " Nm"), cx, cy + sSmall() * 1.3f, sSmall(), fg, Paint.Align.CENTER)
        val range = if (s != null && e != null) "${fmt(s)}-${fmt(e)}°" else "-"
        val peak = if (ps != null && pe != null) "peak ${fmt(ps)}-${fmt(pe)}°" else "peak -"
        label(c, range, cx, cy + r + sSmall() * 1.4f, sSmall(), fg, Paint.Align.CENTER)
        label(c, peak, cx, cy + r + sSmall() * 2.6f, sSmall(), dim, Paint.Align.CENTER)
    }

    /** Platform center offset scale, +-[range] mm, + = outward from the bike. */
    private fun pco(c: Canvas, name: String, value: Double?, color: Int, x: Float, y: Float, w: Float, range: Double = 20.0) {
        label(c, name, x, y, sSmall(), dim)
        label(c, fmt(value, 0, " mm"), x + w, y, sSmall(), fg, Paint.Align.RIGHT)
        val mid = x + w / 2
        val ly = y + sSmall() * 1.2f
        stroke.color = outline
        stroke.strokeWidth = sSmall() * 0.15f
        c.drawLine(x, ly, x + w, ly, stroke)
        c.drawLine(mid, ly - sSmall() * 0.5f, mid, ly + sSmall() * 0.5f, stroke)
        if (value != null) {
            val px = mid + (value / range).coerceIn(-1.0, 1.0).toFloat() * (w / 2)
            fill.color = color
            c.drawCircle(px, ly, sSmall() * 0.6f, fill)
        }
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val pad = w * 0.04f
        val bw = w - 2 * pad
        val half = (bw - w * 0.04f) / 2
        val xr = pad + half + w * 0.04f

        // row 1: power and heart rate, row 2: cadence and rider position
        bigValue(c, fmt(v(Metrics.POWER)), "W", pad, h * 0.100f, maxWidth = w * 0.46f)
        val hr = v(Metrics.HEART_RATE)
        bigValue(c, fmt(hr), "bpm", w * 0.52f, h * 0.100f, if (hr == null) warn else fg, w * 0.44f)
        bigValue(c, fmt(v(Metrics.CADENCE)), "rpm", pad, h * 0.190f, maxWidth = w * 0.46f)
        val posText = when (v(Metrics.RIDER_POSITION)?.toInt()) {
            0 -> "seated"; 1 -> "to seated"; 2 -> "standing"; 3 -> "to standing"; else -> "-"
        }
        label(c, "Rider position", w * 0.52f, h * 0.152f, sSmall(), dim)
        label(c, posText, w * 0.52f, h * 0.190f, sMed())

        // balance and per-pedal watts
        val r = v(Metrics.BALANCE_RIGHT)
        label(c, "Balance L / R", pad, h * 0.240f, sSmall(), dim)
        val bt = h * 0.247f
        val bh = h * 0.040f
        fill.color = track
        c.drawRoundRect(RectF(pad, bt, pad + bw, bt + bh), bh / 4, bh / 4, fill)
        if (r != null) {
            val lw = bw * ((100 - r) / 100).toFloat()
            fill.color = colL
            c.drawRoundRect(RectF(pad, bt, pad + lw, bt + bh), bh / 4, bh / 4, fill)
            fill.color = colR
            c.drawRoundRect(RectF(pad + lw, bt, pad + bw, bt + bh), bh / 4, bh / 4, fill)
            label(c, "L ${fmt(100 - r, 0, " %")}", pad + bh * 0.3f, bt + bh * 0.70f, sMed(), Color.BLACK)
            label(c, "R ${fmt(r, 0, " %")}", pad + bw - bh * 0.3f, bt + bh * 0.70f, sMed(), Color.BLACK, Paint.Align.RIGHT)
        } else {
            label(c, "-", pad + bw / 2, bt + bh * 0.70f, sMed(), dim, Paint.Align.CENTER)
        }
        val pw = v(Metrics.POWER)
        val lWatts = if (pw != null && r != null) pw * (100 - r) / 100 else null
        val rWatts = if (pw != null && r != null) pw * r / 100 else null
        label(c, "L ${fmt(lWatts, 0, " W")}", pad, h * 0.327f, sMed(), colL)
        label(c, "R ${fmt(rWatts, 0, " W")}", pad + bw, h * 0.327f, sMed(), colR, Paint.Align.RIGHT)

        // torque effectiveness and pedal smoothness
        bar(c, "Torque eff. L", v(Metrics.TE_LEFT), colL, pad, h * 0.385f, half)
        bar(c, "Torque eff. R", v(Metrics.TE_RIGHT), colR, xr, h * 0.385f, half)
        val comb = v(Metrics.PS_COMBINED)
        if (comb != null && v(Metrics.PS_LEFT) == null && v(Metrics.PS_RIGHT) == null) {
            bar(c, "Pedal smoothness (combined)", comb, fg, pad, h * 0.450f, bw)
        } else {
            bar(c, "Pedal smooth. L", v(Metrics.PS_LEFT), colL, pad, h * 0.450f, half)
            bar(c, "Pedal smooth. R", v(Metrics.PS_RIGHT), colR, xr, h * 0.450f, half)
        }

        // power phase
        label(c, "Power phase (solid = peak torque)", pad, h * 0.515f, sSmall(), dim)
        val cr = minOf(w * 0.20f, h * 0.072f)
        val cy = h * 0.515f + sSmall() * 1.0f + cr + sSmall() * 0.7f
        crank(c, "L", "left", colL, w * 0.27f, cy, cr)
        crank(c, "R", "right", colR, w * 0.73f, cy, cr)

        // platform center offset
        pco(c, "PCO left", v(Metrics.PCO_LEFT), colL, pad, h * 0.88f, half)
        pco(c, "PCO right", v(Metrics.PCO_RIGHT), colR, xr, h * 0.88f, half)
    }
}
