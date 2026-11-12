package app.sensorsim

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

/** Expected bytes are worked out by hand from the Bluetooth SIG layout, not from the encoder. */
class EncodersTest {
    private fun b(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }

    @Test fun ftmsFeatureBase() = assertArrayEquals(b(0x24, 0x50, 0, 0, 0, 0, 0, 0), Ble.ftmsFeature())

    @Test fun ftmsFeatureExtra() =
        assertArrayEquals(b(0x26, 0x52, 0, 0, 0, 0, 0, 0), Ble.ftmsFeature(RowerFields.EXTRA_FEATURE))

    @Test fun rowerDataBaseLayout() {
        val s = RowSnapshot(strokeRateSpm = 20.5, strokeCount = 300, distanceM = 1234.0, speedMps = 4.0, powerW = 180, elapsedS = 65)
        // the layout that worked in MyWhoosh: flags 0x082C | rate 41 | count 0x012C | distance 0x0004D2 | pace 125 | power 180 | elapsed 65
        assertArrayEquals(b(0x2C, 0x08, 41, 0x2C, 0x01, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0xB4, 0x00, 65, 0x00), Ble.rowerData(s))
    }

    @Test fun rowerDataOneOptionalFieldAtATime() {
        val s = RowSnapshot(strokeRateSpm = 20.5, strokeCount = 300, distanceM = 1234.0, speedMps = 4.0, powerW = 180,
            elapsedS = 65, avgStrokeRateSpm = 19.0, avgPaceS = 130, avgPowerW = 170, energyKcal = 25, kcalPerHour = 620,
            kcalPerMinute = 10)
        // average stroke rate: flags 0x082E, one byte (38) right after the stroke count
        assertArrayEquals(b(0x2E, 0x08, 41, 0x2C, 0x01, 38, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0xB4, 0x00, 65, 0x00),
            Ble.rowerData(s, RowerFields.AVG_RATE))
        // average pace: flags 0x083C, two bytes (130) after the pace
        assertArrayEquals(b(0x3C, 0x08, 41, 0x2C, 0x01, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0x82, 0x00, 0xB4, 0x00, 65, 0x00),
            Ble.rowerData(s, RowerFields.AVG_PACE))
        // average power: flags 0x086C, two bytes (170) after the power
        assertArrayEquals(b(0x6C, 0x08, 41, 0x2C, 0x01, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0xB4, 0x00, 0xAA, 0x00, 65, 0x00),
            Ble.rowerData(s, RowerFields.AVG_POWER))
        // energy: flags 0x092C, total 25, per hour 620 = 0x026C, per minute 10, before the elapsed time
        assertArrayEquals(b(0x2C, 0x09, 41, 0x2C, 0x01, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0xB4, 0x00, 0x19, 0x00, 0x6C, 0x02, 0x0A, 65, 0x00),
            Ble.rowerData(s, RowerFields.ENERGY))
    }

    @Test fun deviceInfoText() = assertArrayEquals(b(0x41, 0x42), Ble.text("AB"))

    @Test fun pace() {
        assertEquals(125, Ble.paceS500(4.0))   // 500 m / 4 m/s
        assertEquals(0, Ble.paceS500(0.0))
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


    @Test fun manualSpeedIsTheSliderAndPowerFollowsIt() {
        val r = ManualRower()
        repeat(40) { r.advance(Targets(3.83, 30.0), 250.0, 17.0) } // 10 s
        val s = r.snapshot()
        assertEquals(3.83, s.speedMps, 1e-9)
        assertEquals(10 * 3.83, s.distanceM, 1e-6)
        assertEquals(131, Ble.paceS500(s.speedMps))                // 2:11
        assertEquals(5L, s.strokeCount)                            // 30 spm = 0.5 per second
        assertEquals(10, s.elapsedS)
        assertEquals(100, s.powerW)                                // 1.78 x 3.83^3 = 100 W, the 17 L line
    }

    @Test fun manualFasterSpeedMeansMorePower() {
        val slow = ManualRower().apply { advance(Targets(3.0, 20.0), 250.0, 17.0) }.snapshot()
        val fast = ManualRower().apply { advance(Targets(4.0, 20.0), 250.0, 17.0) }.snapshot()
        assertEquals(true, fast.powerW > slow.powerW)
        assertEquals(1.78 * 64, fast.powerW.toDouble(), 0.6)       // 4 m/s = 114 W
    }

    @Test fun manualFillSettingChangesPowerAtEqualSpeed() {
        val a = ManualRower().apply { advance(Targets(3.83, 20.0), 250.0, 17.0) }.snapshot()
        val b = ManualRower().apply { advance(Targets(3.83, 20.0), 250.0, 19.0) }.snapshot()
        assertEquals(true, b.powerW > a.powerW)                    // 19 L shows about 11 % more watts
    }

    @Test fun manualZeroSpeedStandsStill() {
        val s = ManualRower().apply { advance(Targets(0.0, 0.0), 250.0, 17.0) }.snapshot()
        assertEquals(0.0, s.speedMps, 0.0)
        assertEquals(0, s.powerW)
    }

    @Test fun rowerDataAllOptionalFields() {
        val s = RowSnapshot(strokeRateSpm = 20.5, strokeCount = 300, distanceM = 1234.0, speedMps = 4.0, powerW = 180,
            elapsedS = 65, avgStrokeRateSpm = 19.0, avgPaceS = 130, avgPowerW = 170, energyKcal = 25, kcalPerHour = 620,
            kcalPerMinute = 10)
        val all = RowerFields.AVG_RATE or RowerFields.AVG_PACE or RowerFields.AVG_POWER or RowerFields.ENERGY
        // flags 0x097E | rate 41 | count 0x012C | avg rate 38 | distance 0x0004D2 | pace 125 | avg pace 130 = 0x82
        // | power 180 | avg power 170 = 0xAA | energy 25, 620 = 0x026C, 10 | elapsed 65
        assertArrayEquals(
            b(0x7E, 0x09, 41, 0x2C, 0x01, 38, 0xD2, 0x04, 0x00, 0x7D, 0x00, 0x82, 0x00, 0xB4, 0x00, 0xAA, 0x00,
                0x19, 0x00, 0x6C, 0x02, 0x0A, 65, 0x00),
            Ble.rowerData(s, all))
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
