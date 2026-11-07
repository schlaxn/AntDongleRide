package app.antlogger

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AntProtocolTest {
    @Test
    fun encodeBuildsSyncLenIdDataChecksum() {
        // RESET_SYSTEM with filler byte 0: A4 01 4A 00 EF
        val raw = Ant.encode(Ant.RESET, byteArrayOf(0))
        assertEquals("A4 01 4A 00 EF", raw.hex())
    }

    @Test
    fun parserHandlesSplitChunksAndGarbage() {
        val frame = Ant.encode(Ant.BROADCAST, byteArrayOf(0, 0x10, 1, 2, 3, 4, 5, 6, 7))
        val p = FrameParser()
        val out = ArrayList<Frame>()
        out += p.feed(byteArrayOf(0x00, 0x13) + frame.copyOfRange(0, 5)) // junk + first half
        out += p.feed(frame.copyOfRange(5, frame.size))
        assertEquals(1, out.size)
        assertEquals(Ant.BROADCAST, out[0].msgId)
        assertArrayEquals(byteArrayOf(0, 0x10, 1, 2, 3, 4, 5, 6, 7), out[0].data)
    }

    @Test
    fun readOnlyGuardAllowsOnlyStickConfigurationMessages() {
        // everything the session sends to configure the stick passes
        for (id in Ant.STICK_CONFIG_MSGS) Ant.requireReadOnly(Ant.encode(id, byteArrayOf(0, 0, 0)))
    }

    @Test
    fun readOnlyGuardRefusesAnythingThatCouldReachASensor() {
        val dataToSensor = byteArrayOf(0, 0x46, 0xFF.toByte(), 0xFF.toByte(), 0xFE.toByte(), 0xFF.toByte(), 4, 2, 1)
        for (id in listOf(Ant.BROADCAST, Ant.ACK_DATA, Ant.BURST, 0x72 /* advanced burst */, 0x5E, 0x5F, 0x4D)) {
            val raw = Ant.encode(id, dataToSensor)
            val e = runCatching { Ant.requireReadOnly(raw) }.exceptionOrNull()
            assertTrue("0x%02X must be refused".format(id), e is IllegalStateException)
        }
        assertTrue(runCatching { Ant.requireReadOnly(byteArrayOf()) }.isFailure) // malformed
        assertTrue(runCatching { Ant.requireReadOnly(byteArrayOf(0x00, 0x01, 0x4A, 0x00, 0x00)) }.isFailure) // no sync
    }

    @Test
    fun parserCountsBadChecksum() {
        val frame = Ant.encode(Ant.BROADCAST, byteArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 8))
        frame[frame.size - 1] = (frame[frame.size - 1].toInt() xor 0xFF).toByte()
        val p = FrameParser()
        assertEquals(0, p.feed(frame).size)
        assertEquals(1, p.badChecksums)
    }
}
