package app.sensorsim

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

/** Expected bytes are worked out by hand from the Bluetooth SIG layout, not from the encoder. */
class EncodersTest {
    private fun b(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }

    @Test fun ftmsFeature() = assertArrayEquals(b(0x24, 0x50, 0, 0, 0, 0, 0, 0), Ble.ftmsFeature())

    @Test fun deviceInfoText() = assertArrayEquals(b(0x41, 0x42), Ble.text("AB"))

    @Test fun pace() {
        assertEquals(125, Ble.paceS500(4.0))   // 500 m / 4 m/s
        assertEquals(0, Ble.paceS500(0.0))
    }

    @Test fun rowerDataWorkingLayout() {
        val s = RowSnapshot(strokeRateSpm = 20.5, strokeCount = 300, distanceM = 1234.0, speedMps = 4.0, powerW = 180, elapsedS = 65)
        // flags 0x082C | rate 41 | count 0x012C | distance 0x0004D2 | pace 125 = 0x7D | power 180 = 0xB4 | elapsed 65
        assertArrayEquals(b(0x2C, 0x08, 41, 0x2C, 0x01, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0xB4, 0x00, 65, 0x00), Ble.rowerData(s))
    }

    @Test fun rowerDataStrokeCountWrapsAt16Bits() {
        val d = Ble.rowerData(RowSnapshot(0.0, 65541, 0.0, 0.0, 0, 0))
        assertArrayEquals(b(0x05, 0x00), d.copyOfRange(3, 5))
    }

    @Test fun controlPointAcceptsControlResetStartStop() {
        assertArrayEquals(b(0x80, 0x00, 0x01), Ble.controlPointReply(0x00))
        assertArrayEquals(b(0x80, 0x01, 0x01), Ble.controlPointReply(0x01))
        assertArrayEquals(b(0x80, 0x07, 0x01), Ble.controlPointReply(0x07))
        assertArrayEquals(b(0x80, 0x08, 0x01), Ble.controlPointReply(0x08))
    }

    @Test fun controlPointRefusesTheRest() {
        assertArrayEquals(b(0x80, 0x11, 0x02), Ble.controlPointReply(0x11))  // bike simulation
        assertArrayEquals(b(0x80, 0x05, 0x02), Ble.controlPointReply(0x05))  // target power
    }

    @Test fun revCounterCountsAndTimesTheLastStroke() {
        val c = RevCounter()
        repeat(4) { c.advance(1.0, (it + 1) * 250.0, 250.0) }   // 1 stroke per second for 1 s
        assertEquals(1L, c.count)
        assertEquals(1024, c.eventTicks)                         // the stroke completed at 1000 ms
        c.advance(0.0, 2000.0, 1000.0)
        assertEquals(1L, c.count)
    }

    @Test fun cpsMeasurementBytes() {
        // flags 0x0020, power 250 = 0x00FA, crank revs 10, crank event time 1024 = 0x0400
        assertArrayEquals(b(0x20, 0x00, 0xFA, 0x00, 0x0A, 0x00, 0x00, 0x04), Ble.cpsMeasurement(250, 10, 1024))
    }


    @Test fun manualSpeedFollowsPowerWithTheCubeLaw() {
        val r = ManualRower()
        repeat(40) { r.advance(Targets(100, 30.0), 250.0, 17.0) } // 10 s
        val s = r.snapshot()
        val v = Math.cbrt(100 / 1.78)                              // 3.83 m/s, the 100 W line of ROWER_INTERFACE.md
        assertEquals(v, s.speedMps, 1e-9)
        assertEquals(10 * v, s.distanceM, 1e-6)
        assertEquals(131, Ble.paceS500(s.speedMps))                // 2:11
        assertEquals(5L, s.strokeCount)                            // 30 spm = 0.5 per second
        assertEquals(10, s.elapsedS)
        assertEquals(100, s.powerW)
    }

    @Test fun manualMorePowerMeansFasterPace() {
        val slow = ManualRower().apply { advance(Targets(100, 20.0), 250.0, 17.0) }.snapshot()
        val fast = ManualRower().apply { advance(Targets(200, 20.0), 250.0, 17.0) }.snapshot()
        assertEquals(true, Ble.paceS500(fast.speedMps) < Ble.paceS500(slow.speedMps))
    }

    @Test fun manualFillSettingChangesSpeedAtEqualPower() {
        val a = ManualRower().apply { advance(Targets(100, 20.0), 250.0, 17.0) }.snapshot()
        val b = ManualRower().apply { advance(Targets(100, 20.0), 250.0, 19.0) }.snapshot()
        assertEquals(true, b.speedMps < a.speedMps)               // 3.70 against 3.83 m/s
    }

    @Test fun manualZeroPowerStandsStill() {
        val s = ManualRower().apply { advance(Targets(0, 0.0), 250.0, 17.0) }.snapshot()
        assertEquals(0.0, s.speedMps, 0.0)
    }

    @Test fun sessionStatsAveragesAndEnergy() {
        val stats = SessionStats(0.25)
        val s = RowSnapshot(30.0, 5, 40.0, 4.0, 200, 10)
        repeat(40) { stats.add(s, 0.25) }                         // 10 s at 200 W = 2000 J
        val x = stats.extend(s)
        assertEquals(30.0, x.avgStrokeRateSpm, 1e-9)              // 5 strokes in 10 s
        assertEquals(125, x.avgPaceS)                             // 500 * 10 s / 40 m
        assertEquals(200, x.avgPowerW)
        assertEquals(2, x.energyKcal)                             // 2000 J / 4184 / 0.25 = 1.91 kcal
        assertEquals(688, x.kcalPerHour)                          // 200 W * 3600 / 4184 / 0.25
        assertEquals(11, x.kcalPerMinute)
    }

    @Test fun sessionStatsIgnoreIdleTime() {
        val stats = SessionStats(0.25)
        val idle = RowSnapshot(0.0, 0, 0.0, 0.0, 0, 0)
        repeat(100) { stats.add(idle, 0.25) }
        assertEquals(0, stats.extend(idle).avgPowerW)
    }
}
