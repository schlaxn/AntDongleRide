package app.antlogger

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
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
    fun parserCountsBadChecksum() {
        val frame = Ant.encode(Ant.BROADCAST, byteArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 8))
        frame[frame.size - 1] = (frame[frame.size - 1].toInt() xor 0xFF).toByte()
        val p = FrameParser()
        assertEquals(0, p.feed(frame).size)
        assertEquals(1, p.badChecksums)
    }
}
