package app.antlogger

import org.junit.Assert.assertEquals
import org.junit.Test

class FormatValueTest {
    @Test
    fun percentUnitDoesNotCrashAndIsAppended() {
        // this is the exact case that crashed the Live page: a bare '%' in the unit
        assertEquals("50 %", formatValue(50.0, 0, " %"))
        assertEquals("100 %", formatValue(100.0, 0, " %"))
    }

    @Test
    fun nullShowsDash() {
        assertEquals("-", formatValue(null, 1, " %"))
        assertEquals("-", formatValue(null))
    }

    @Test
    fun unitWithoutPercentWorks() {
        assertEquals("250 W", formatValue(250.0, 0, " W"))
        assertEquals("12 rpm", formatValue(12.0, 0, " rpm"))
    }

    @Test
    fun unitWithPercentSignsDoesNotBreakFormatting() {
        assertEquals("1%%", formatValue(1.0, 0, "%%"))
    }
}
