package app.antlogger

import com.garmin.fit.Decode
import com.garmin.fit.MesgBroadcaster
import com.garmin.fit.RecordMesgListener
import com.garmin.fit.SessionMesgListener
import java.io.File
import java.io.FileInputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DecoderFitTest {
    private fun bytes(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }

    @Test
    fun heartRatePageGivesBpmFromByte7() {
        val s = Decoder.decode(1000, Decoder.DEV_HR, 17691, bytes(0x80, 0xFF, 0xFF, 0xFF, 0xB6, 0x35, 0x3E, 0x3C))
        assertEquals(1, s.size)
        assertEquals("heart_rate", s[0].metric)
        assertEquals(60.0, s[0].value, 0.0)
    }

    @Test
    fun heartRateZeroIsInvalid() {
        assertTrue(Decoder.decode(0, Decoder.DEV_HR, 1, bytes(0, 0, 0, 0, 0, 0, 0, 0)).isEmpty())
    }

    @Test
    fun powerPage10GivesPowerCadenceBalance() {
        // event count 5, balance right 48 % (0x80|48), cadence 90, accum power 0x1234, power 250 W
        val s = Decoder.decode(0, Decoder.DEV_POWER, 7, bytes(0x10, 5, 0x80 or 48, 90, 0x34, 0x12, 250, 0))
        val m = s.associate { it.metric to it.value }
        assertEquals(250.0, m["power"]!!, 0.0)
        assertEquals(90.0, m["cadence"]!!, 0.0)
        assertEquals(48.0, m["pedal_balance_right"]!!, 0.0)
    }

    @Test
    fun powerPageWithInvalidBalanceAndCadenceOmitsThem() {
        val m = Decoder.decode(0, Decoder.DEV_POWER, 7, bytes(0x10, 1, 0xFF, 0xFF, 0, 0, 100, 0))
            .associate { it.metric to it.value }
        assertEquals(setOf("power"), m.keys)
    }

    private fun power(vararg b: Int) =
        Decoder.decode(0, Decoder.DEV_POWER, 7, bytes(*b)).associate { it.metric to it.value }

    @Test
    fun page13TorqueEffectivenessAndPedalSmoothnessInHalfPercent() {
        // L TE 80 -> 40 %, R TE 90 -> 45 %, L PS 160 -> 80 %, R PS 170 -> 85 %
        val m = power(0x13, 1, 80, 90, 160, 170, 0xFF, 0xFF)
        assertEquals(40.0, m[Metrics.TE_LEFT]!!, 0.0)
        assertEquals(45.0, m[Metrics.TE_RIGHT]!!, 0.0)
        assertEquals(80.0, m[Metrics.PS_LEFT]!!, 0.0)
        assertEquals(85.0, m[Metrics.PS_RIGHT]!!, 0.0)
    }

    @Test
    fun page13CombinedSmoothnessWhenByte5IsFE() {
        val m = power(0x13, 1, 0xFF, 0xFF, 150, 0xFE, 0xFF, 0xFF)
        assertEquals(75.0, m[Metrics.PS_COMBINED]!!, 0.0)
        assertEquals(null, m[Metrics.PS_LEFT])
        assertEquals(null, m[Metrics.TE_LEFT]) // 0xFF = invalid
    }

    @Test
    fun pageE0RightForceAngleBradsToDegrees() {
        // start 64 brads = 90 deg, end 192 = 270 deg, peak 96 = 135 deg .. 160 = 225 deg, torque 0x0140 = 10 Nm
        val m = power(0xE0, 3, 64, 192, 96, 160, 0x40, 0x01)
        assertEquals(90.0, m[Metrics.fa("right", "start_deg")]!!, 1e-9)
        assertEquals(270.0, m[Metrics.fa("right", "end_deg")]!!, 1e-9)
        assertEquals(135.0, m[Metrics.fa("right", "peak_start_deg")]!!, 1e-9)
        assertEquals(225.0, m[Metrics.fa("right", "peak_end_deg")]!!, 1e-9)
        assertEquals(10.0, m[Metrics.fa("right", "torque_nm")]!!, 1e-9)
    }

    @Test
    fun pageE1IsLeftAndC0C0MeansInvalidAngles() {
        val m = power(0xE1, 3, 0xC0, 0xC0, 0xC0, 0xC0, 0, 0)
        assertEquals(setOf(Metrics.fa("left", "torque_nm")), m.keys)
    }

    @Test
    fun pageE2PedalPositionRiderPositionCadenceAndPco() {
        // byte 2 bits 6-7 = 2 (standing); cadence 90; right PCO +5 mm; left PCO -3 mm (0xFD)
        val m = power(0xE2, 9, 0x80, 90, 5, 0xFD, 0xFF, 0xFF)
        assertEquals(2.0, m[Metrics.RIDER_POSITION]!!, 0.0)
        assertEquals(90.0, m[Metrics.CADENCE]!!, 0.0)
        assertEquals(5.0, m[Metrics.PCO_RIGHT]!!, 0.0)
        assertEquals(-3.0, m[Metrics.PCO_LEFT]!!, 0.0)
    }

    @Test
    fun pageE2PcoMinus128IsInvalid() {
        val m = power(0xE2, 9, 0x00, 0xFF, 0x80, 0x80, 0xFF, 0xFF)
        assertEquals(0.0, m[Metrics.RIDER_POSITION]!!, 0.0)
        assertEquals(setOf(Metrics.RIDER_POSITION), m.keys)
    }

    @Test
    fun capabilitiesPageReportsWhichDynamicsTheSensorHasSwitchedOn() {
        // sensor supports 8 Hz, power phase, PCO, rider position, barycenter (mask bits 1,3,4,5,6 = 0).
        // value: bit0 = 1 (not 4 Hz), bit1 = 0 (8 Hz on), bit3 = 0 (power phase on), bit4 = 0 (PCO on),
        // bit5 = 1 (rider position off), bit6 = 1 (barycenter off), bits 2 and 7 always 1 -> 0xE5
        val m = power(0x02, 0xFE, 0xFF, 0xFF, 0x85, 0xFF, 0xE5, 0xFF)
        assertEquals(1.0, m[Metrics.DYN_8HZ]!!, 0.0)
        assertEquals(1.0, m[Metrics.DYN_POWER_PHASE]!!, 0.0)
        assertEquals(1.0, m[Metrics.DYN_PCO]!!, 0.0)
        assertEquals(0.0, m[Metrics.DYN_RIDER_POSITION]!!, 0.0)
        assertEquals(0.0, m[Metrics.DYN_BARYCENTER]!!, 0.0)
    }

    @Test
    fun capabilitiesPageOmitsSettingsTheSensorDoesNotSupport() {
        // mask bit 4 set = PCO not supported; everything else supported
        val m = power(0x02, 0xFE, 0xFF, 0xFF, 0x95, 0xFF, 0xFF, 0xFF)
        assertEquals(null, m[Metrics.DYN_PCO])
        assertEquals(0.0, m[Metrics.DYN_POWER_PHASE]!!, 0.0) // value bit 3 = 1: off
    }

    @Test
    fun otherSubpagesOfPage02AreNotInterpreted() {
        assertTrue(power(0x02, 0xFD, 0xFF, 0xFF, 0x00, 0xFF, 0x00, 0xFF).isEmpty())
    }

    @Test
    fun page14TorqueBarycenterStartsAt30DegreesInHalfDegrees() {
        assertEquals(40.0, power(0x14, 20, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF)[Metrics.BARYCENTER]!!, 1e-9)
    }

    @Test
    fun commonPage52BatteryVoltageAndStatus() {
        // fractional 128/256 = 0.5 V, coarse 3 V, status 2 (good)
        val m = power(0x52, 0, 0x01, 0, 0, 0, 128, 0x23)
        assertEquals(3.5, m[Metrics.BATTERY_V]!!, 1e-9)
        assertEquals(2.0, m[Metrics.BATTERY_STATUS]!!, 0.0)
    }

    private fun csc(t: SensorTracker, ts: Long, devType: Int, vararg b: Int) =
        t.decode(ts, devType, 5, bytes(*b)).associate { it.metric to it.value }

    @Test
    fun combinedSensorGivesCadenceSpeedAndDistance() {
        val t = SensorTracker(2.1)
        // first page only primes the counters
        assertTrue(csc(t, 0, 121, 0, 0, 0, 0, 0, 0, 0, 0).isEmpty())
        // 1 revolution in 768/1024 s = 0.75 s: cadence 80 rpm, speed 2.1 m / 0.75 s = 2.8 m/s = 10.08 km/h
        val m = csc(t, 750, 121, 0x00, 0x03, 1, 0, 0x00, 0x03, 1, 0)
        assertEquals(80.0, m[Metrics.CSC_CADENCE]!!, 1e-9)
        assertEquals(10.08, m[Metrics.SPEED_KMH]!!, 1e-9)
        assertEquals(2.1, m[Metrics.DISTANCE_M]!!, 1e-9)
    }

    @Test
    fun speedOnlySensorUsesBytes4to7AndCounterWrapIsHandled() {
        val t = SensorTracker(2.0)
        csc(t, 0, 123, 0, 0, 0, 0, 0xDC, 0xFF, 0xFF, 0xFF) // time 0xFFDC = 65500, revs 0xFFFF
        // time wraps to 0x00C8 = 200: dT = 236 ticks; revs wrap to 1: dR = 2
        val m = csc(t, 230, 123, 0, 0, 0, 0, 0xC8, 0x00, 0x01, 0x00)
        val rps = 2 / (236 / 1024.0)
        assertEquals(rps * 2.0 * 3.6, m[Metrics.SPEED_KMH]!!, 1e-9)
    }

    @Test
    fun noNewEventForOverThreeSecondsMeansStandingStill() {
        val t = SensorTracker(2.1)
        csc(t, 0, 122, 0, 0, 0, 0, 100, 0, 5, 0)
        assertTrue(csc(t, 1000, 122, 0, 0, 0, 0, 100, 0, 5, 0).isEmpty()) // same event, too early to call it
        assertEquals(0.0, csc(t, 4500, 122, 0, 0, 0, 0, 100, 0, 5, 0)[Metrics.CSC_CADENCE]!!, 0.0)
    }

    @Test
    fun heartRateBeatIntervalFromConsecutiveBeats() {
        val t = SensorTracker()
        // beat count 10 at time 1000, then count 11 at time 2024: 1024 ticks = exactly 1000 ms
        t.decode(0, 120, 9, bytes(0x80, 0, 0, 0, 1000 and 0xFF, 1000 shr 8, 10, 60))
        val m = t.decode(1000, 120, 9, bytes(0x00, 0, 0, 0, 2024 and 0xFF, 2024 shr 8, 11, 60))
            .associate { it.metric to it.value }
        assertEquals(1000.0, m[Metrics.RR_MS]!!, 1e-9)
        assertEquals(60.0, m[Metrics.HEART_RATE]!!, 0.0)
    }

    @Test
    fun captureReaderKeepsOnlyTheRideBetweenMarkers() {
        fun hrLine(ts: Long, bpm: Int): String {
            val f = Ant.encode(Ant.BROADCAST, bytes(0, 0x80, 0xFF, 0xFF, 0xFF, 0, 0, 1, bpm, 0x80, 0x1B, 0x45, 120, 0x31))
            return "$ts,${f.hex().replace(" ", "")}"
        }
        val lines = sequenceOf(hrLine(1000, 50), "1500,RIDE_START", hrLine(2000, 60), hrLine(3000, 70),
            "3500,RIDE_STOP", hrLine(4000, 80))
        val hr = CaptureReader.read(lines).filter { it.metric == Metrics.HEART_RATE }.map { it.value }
        assertEquals(listOf(60.0, 70.0), hr)
    }

    @Test
    fun captureWithTwoRidesExportsTheLatestOne() {
        fun hrLine(ts: Long, bpm: Int): String {
            val f = Ant.encode(Ant.BROADCAST, bytes(0, 0x80, 0xFF, 0xFF, 0xFF, 0, 0, 1, bpm, 0x80, 0x1B, 0x45, 120, 0x31))
            return "$ts,${f.hex().replace(" ", "")}"
        }
        val lines = sequenceOf("1000,RIDE_START", hrLine(1500, 60), "2000,RIDE_STOP",
            hrLine(2500, 65), "3000,RIDE_START", hrLine(3500, 70), "4000,RIDE_STOP")
        val hr = CaptureReader.read(lines).filter { it.metric == Metrics.HEART_RATE }.map { it.value }
        assertEquals(listOf(70.0), hr)
    }

    @Test
    fun rideInfoTellsFinishedFromUnfinishedAndNone() {
        val none = CaptureReader.rideInfo(sequenceOf("1000,A40340004600A1"))
        assertEquals(null, none.startMs)

        val unfinished = CaptureReader.rideInfo(sequenceOf("1000,RIDE_START", "1500,A40340004600A1"))
        assertEquals(1000L, unfinished.startMs)
        assertEquals(false, unfinished.finished)

        val finished = CaptureReader.rideInfo(sequenceOf("1000,RIDE_START", "2000,RIDE_STOP"))
        assertEquals(true, finished.finished)

        // a second ride after a finished one, never stopped: the latest ride decides
        val second = CaptureReader.rideInfo(sequenceOf("1000,RIDE_START", "2000,RIDE_STOP", "3000,RIDE_START"))
        assertEquals(3000L, second.startMs)
        assertEquals(false, second.finished)
    }

    @Test
    fun unfinishedRideExportsEverythingAfterItsStart() {
        fun hrLine(ts: Long, bpm: Int): String {
            val f = Ant.encode(Ant.BROADCAST, bytes(0, 0x80, 0xFF, 0xFF, 0xFF, 0, 0, 1, bpm, 0x80, 0x1B, 0x45, 120, 0x31))
            return "$ts,${f.hex().replace(" ", "")}"
        }
        // no RIDE_STOP: the app died mid-ride, the last line may even be cut off
        val lines = sequenceOf(hrLine(500, 40), "1000,RIDE_START", hrLine(1500, 60), hrLine(2500, 70), "2600,A4")
        val hr = CaptureReader.read(lines).filter { it.metric == Metrics.HEART_RATE }.map { it.value }
        assertEquals(listOf(60.0, 70.0), hr)
    }

    @Test
    fun distanceRestartsAtZeroAfterResetRequest() {
        val t = SensorTracker(2.0)
        csc(t, 0, 123, 0, 0, 0, 0, 0, 0, 0, 0)
        val first = csc(t, 100, 123, 0, 0, 0, 0, 0x00, 0x04, 5, 0) // 5 revs = 10 m
        assertEquals(10.0, first[Metrics.DISTANCE_M]!!, 1e-9)
        t.requestDistanceReset()
        val second = csc(t, 200, 123, 0, 0, 0, 0, 0x00, 0x08, 8, 0) // 3 more revs = 6 m since the reset
        assertEquals(6.0, second[Metrics.DISTANCE_M]!!, 1e-9)
    }

    @Test
    fun fitRoundTripCarriesCyclingDynamicsFields() {
        val t0 = 1_794_042_000_000L
        val samples = ArrayList<Sample>()
        fun s(m: String, v: Double) { samples += Sample(t0, 11, 7, m, v) }
        s(Metrics.POWER, 250.0); s(Metrics.CADENCE, 90.0); s(Metrics.BALANCE_RIGHT, 48.0)
        s(Metrics.TE_LEFT, 40.0); s(Metrics.TE_RIGHT, 45.0); s(Metrics.PS_LEFT, 80.0); s(Metrics.PS_RIGHT, 85.0)
        s(Metrics.PCO_LEFT, -3.0); s(Metrics.PCO_RIGHT, 5.0)
        s(Metrics.fa("left", "start_deg"), 270.0); s(Metrics.fa("left", "end_deg"), 90.0)
        val out = File.createTempFile("dyn", ".fit")
        out.deleteOnExit()
        FitExporter.export(samples, out)
        val recs = ArrayList<com.garmin.fit.RecordMesg>()
        val bc = MesgBroadcaster(Decode())
        bc.addListener(RecordMesgListener { recs.add(it) })
        bc.run(FileInputStream(out))
        val r = recs[0]
        assertEquals(250, r.power)
        assertEquals((0x80 or 48).toShort(), r.leftRightBalance)
        assertEquals(40.0f, r.leftTorqueEffectiveness, 0.1f)
        assertEquals(85.0f, r.rightPedalSmoothness, 0.1f)
        assertEquals((-3).toByte(), r.leftPco)
        assertEquals(5.toByte(), r.rightPco)
        assertEquals(270.0f, r.getLeftPowerPhase(0), 1.0f)
        assertEquals(90.0f, r.getLeftPowerPhase(1), 1.0f)
    }

    @Test
    fun captureReaderParsesBroadcastWithExtendedId() {
        // ch0 broadcast, HR page, bpm 0x3C, extended flag 0x80, devnum 17691 (0x451B), devtype 120
        val frame = Ant.encode(Ant.BROADCAST, bytes(0, 0x80, 0xFF, 0xFF, 0xFF, 0xB6, 0x35, 0x3E, 0x3C, 0x80, 0x1B, 0x45, 120, 0x31))
        val csv = sequenceOf("1794042000000,${frame.hex().replace(" ", "")}", "garbage", "17940420010")
        val s = CaptureReader.read(csv)
        assertEquals(1, s.size)
        assertEquals(17691, s[0].devNum)
        assertEquals(60.0, s[0].value, 0.0)
    }

    @Test
    fun fitRoundTripKeepsHeartRateAndShowsGapAsMissing() {
        val t0 = 1_794_042_000_000L
        val samples = ArrayList<Sample>()
        for (i in 0..9) samples += Sample(t0 + i * 1000L, 120, 1, "heart_rate", 60.0 + i)
        // 10 s dropout, then data again
        for (i in 20..24) samples += Sample(t0 + i * 1000L, 120, 1, "heart_rate", 80.0)
        val out = File.createTempFile("test", ".fit")
        out.deleteOnExit()
        val result = FitExporter.export(samples, out)!!
        assertEquals(25, result.records)
        assertEquals(25, result.seconds)

        assertTrue(Decode().checkFileIntegrity(FileInputStream(out)))
        val hr = ArrayList<Short?>()
        var sessionMax: Short? = null
        val d = Decode()
        val bc = MesgBroadcaster(d)
        bc.addListener(RecordMesgListener { hr.add(it.heartRate) })
        bc.addListener(SessionMesgListener { sessionMax = it.maxHeartRate })
        bc.run(FileInputStream(out))
        assertEquals(25, hr.size)
        assertEquals(60.toShort(), hr[0])
        assertEquals(69.toShort(), hr[9])
        assertEquals(69.toShort(), hr[11]) // held for 3 s after last sample at second 9
        assertNull(hr[13]) // beyond the hold time: left out, so Garmin shows a gap
        assertEquals(80.toShort(), hr[24])
        assertEquals(80.toShort(), sessionMax)
    }
}
