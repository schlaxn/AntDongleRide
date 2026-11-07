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
