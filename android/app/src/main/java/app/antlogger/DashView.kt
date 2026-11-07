package app.antlogger

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View

/**
 * Base for the live pages. Everything is sized from the view's own width and height (no fixed dp/px),
 * so the pages fill any display without scrolling. Values older than [STALE_MS] show as "-".
 */
abstract class DashView(context: Context, protected val session: () -> AntSession?) : View(context) {
    // true black background (OLED), light text; left/right colours brightened to read on black
    protected val fg = Color.rgb(235, 235, 235)
    protected val dim = Color.rgb(150, 150, 150)
    protected val colL = Color.rgb(70, 165, 255)
    protected val colR = Color.rgb(255, 150, 40)
    protected val track = Color.rgb(45, 45, 45)
    protected val outline = Color.rgb(85, 85, 85)
    protected val warn = Color.rgb(255, 90, 90)
    protected val ok = Color.rgb(90, 220, 120)

    protected val text = Paint(Paint.ANTI_ALIAS_FLAG)
    protected val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    protected val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

    /** Base text unit in px: the smaller of width/26 and height/48 (about 14 dp on a phone). */
    protected open fun unit() = minOf(width / 26f, height / 48f)
    protected fun sSmall() = unit() * 1.1f // smallest text on any dashboard: about 15 dp
    protected fun sMed() = unit() * 1.5f
    protected fun sBig() = unit() * 4.6f

    protected fun v(metric: String, staleMs: Long = STALE_MS): Double? {
        val p = session()?.live?.get(metric) ?: return null
        return if (System.currentTimeMillis() - p.second <= staleMs) p.first else null
    }

    protected fun fmt(x: Double?, digits: Int = 0, unit: String = "") =
        if (x == null) "-" else "%.${digits}f$unit".format(x)

    protected fun label(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int = fg, align: Paint.Align = Paint.Align.LEFT) {
        text.textSize = size
        text.color = color
        text.textAlign = align
        c.drawText(s, x, y, text)
    }

    /** A big number with its unit right behind it. Returns the x where the unit ends. */
    protected fun bigValue(c: Canvas, value: String, unit: String, x: Float, baseline: Float, color: Int = fg, maxWidth: Float = Float.MAX_VALUE): Float {
        var size = sBig()
        text.textSize = size
        val uw = run { text.textSize = sMed(); text.measureText(unit) + sMed() * 0.3f }
        text.textSize = size
        while (text.measureText(value) + uw > maxWidth && size > unit() * 1.5f) {
            size *= 0.9f
            text.textSize = size
        }
        label(c, value, x, baseline, size, color)
        val vw = text.measureText(value)
        label(c, unit, x + vw + sMed() * 0.3f, baseline, sMed(), dim)
        return x + vw + uw
    }

    /** A 0-100 % bar: name above, value at the right end of that line, bar below. */
    protected fun bar(c: Canvas, name: String, value: Double?, color: Int, x: Float, y: Float, w: Float) {
        label(c, name, x, y, sSmall(), dim)
        label(c, fmt(value, 1, " %"), x + w, y, sSmall(), fg, Paint.Align.RIGHT)
        val top = y + sSmall() * 0.4f
        val h = sSmall() * 0.9f
        fill.color = track
        c.drawRoundRect(RectF(x, top, x + w, top + h), h / 3, h / 3, fill)
        if (value != null) {
            fill.color = color
            c.drawRoundRect(RectF(x, top, x + w * (value / 100.0).coerceIn(0.0, 1.0).toFloat(), top + h), h / 3, h / 3, fill)
        }
    }

    companion object {
        const val STALE_MS = 4000L
    }
}
