package app.sensorsim

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Expected values follow ROWER_INTERFACE.md (91.8 ticks per metre, W = 1.78 v^3, fill factor 1 + 0.054 per litre). */
class S4Test {
    @Test fun parsesLines() {
        assertEquals(S4Line.Pulse(10), parseS4Line("P0A"))
        assertEquals(S4Line.Pulse(16), parseS4Line("P10"))
        assertEquals(S4Line.Pulse(1), parseS4Line("P01\r"))
        assertEquals(S4Line.StrokeStart, parseS4Line("SS"))
        assertEquals(S4Line.StrokeEnd, parseS4Line("SE"))
        assertEquals(S4Line.Ping, parseS4Line("PING"))
        assertEquals(S4Line.Other, parseS4Line("AKR"))
        assertEquals(S4Line.Other, parseS4Line("P1"))
        assertEquals(S4Line.Other, parseS4Line(""))
    }

    @Test fun splitsLinesAcrossReads() {
        val s = LineSplitter()
        val a = "SS\r\nP0".toByteArray()
        assertEquals(listOf("SS"), s.feed(a, a.size))
        val b = "A\r\nPING\r\n".toByteArray()
        assertEquals(listOf("P0A", "PING"), s.feed(b, b.size))
    }

    @Test fun onlyTheHandshakeMayBeSent() {
        S4.requireHandshakeOnly(S4.HANDSHAKE)
        for (bad in listOf("IV?\r\n", "USB", "reset\r\n", "")) {
            try {
                S4.requireHandshakeOnly(bad.toByteArray())
                fail("allowed: $bad")
            } catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun fillFactorIsTheLineThrough17Litres() {
        assertEquals(1.0, S4.fillFactor(17.0), 1e-12)
        assertEquals(1.108, S4.fillFactor(19.0), 1e-9)
        assertEquals(0.892, S4.fillFactor(15.0), 1e-9)
        assertEquals(1.162, S4.fillFactor(20.0), 1e-9)
    }

    @Test fun distanceCountsTicksFromTheFirstStrokeOnly() {
        val d = S4Decoder()
        d.onLine("P0A", 0.0)                       // 10 ticks before the first stroke: not counted
        d.onLine("SS", 1.0)
        repeat(9) { d.onLine("P66", 1.0 + 0.1 * (it + 1)) } // 0x66 = 102 ticks each, 918 in all = 10.0 m
        assertEquals(10.0, d.snapshot(2.0).distanceM, 1e-9)
    }

    @Test fun speedIsAveragedOverAtLeastFifteenSeconds() {
        val d = S4Decoder()
        for (i in 0 until 10) d.onLine("P0A", 0.1 * i)     // 100 ticks in 0.9 s
        // only 1 s has passed since the first pulse, so the average covers 1 s (the minimum divisor)
        assertEquals(100 / 1.0 / 91.8, d.snapshot(1.0).speedMps, 1e-9)
        // at 20 s the pulses are older than the 15 s window: standing still
        assertEquals(0.0, d.snapshot(20.0).speedMps, 1e-12)
    }

    @Test fun speedSpreadsAcrossTheWindow() {
        val d = S4Decoder()
        // 10 lines a second of 10 ticks: at t = 15 s the pulse at t = 0 has left the window; 149 pulses = 1490 ticks over 15 s
        for (i in 0 until 150) d.onLine("P0A", 0.1 * i)
        assertEquals(1490.0 / 15 / 91.8, d.snapshot(15.0).speedMps, 1e-9)
    }

    @Test fun strokeRateFromStrokeIntervals() {
        val d = S4Decoder()
        d.onLine("SS", 0.0)
        d.onLine("SS", 2.0)                         // 2 s per stroke = 30 spm
        assertEquals(30.0, d.snapshot(2.1).strokeRateSpm, 1e-9)
        assertEquals(2L, d.snapshot(2.1).strokeCount)
        assertEquals(0.0, d.snapshot(10.0).strokeRateSpm, 1e-12)   // no stroke for over 5 s
    }

    private fun steadyRowing(d: S4Decoder) {
        // 40 lines a second of 5 ticks (200 ticks/s), a stroke every 3 s, for 6 s
        var t = 0.0
        d.onLine("SS", 0.0)
        for (k in 1..240) {
            t = k / 40.0
            d.onLine("P05", t)
            if (k % 120 == 0) d.onLine("SS", t)
        }
    }

    @Test fun powerFollowsTheCubeLawAt17Litres() {
        val d = S4Decoder(17.0)
        steadyRowing(d)
        val v = 200 / 91.8
        val expected = 1.78 * v * v * v                    // about 18.4 W
        // the 1 s window holds 40 or 41 lines depending on floating-point timestamps, hence the tolerance
        assertEquals(expected, d.snapshot(6.0).powerW.toDouble(), 2.5)
    }

    @Test fun powerScalesWithTheFillSetting() {
        val a = S4Decoder(17.0); steadyRowing(a)
        val b = S4Decoder(19.0); steadyRowing(b)
        val ratio = b.snapshot(6.0).powerW / a.snapshot(6.0).powerW.toDouble()
        assertTrue("ratio $ratio", ratio > 1.09 && ratio < 1.13)   // 1.108
    }

    @Test fun elapsedFreezesWhenTheFlywheelStops() {
        val d = S4Decoder()
        d.onLine("SS", 10.0)
        d.onLine("P05", 70.0)
        assertEquals(60, d.snapshot(200.0).elapsedS)
    }

    @Test fun resetClearsTheSession() {
        val d = S4Decoder()
        steadyRowing(d)
        d.reset()
        val s = d.snapshot(7.0)
        assertEquals(0L, s.strokeCount)
        assertEquals(0.0, s.distanceM, 1e-12)
        assertEquals(0, s.powerW)
    }
}
