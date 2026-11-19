package app.sensorsim

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

class ActivityRecorderTest {
    @Test fun writesAFitFileWithOneRecordPerSecond() {
        val r = ActivityRecorder()
        val t0 = 1_700_000_000_000L
        for (i in 0 until 30) r.add(ActivityRecorder.Point(t0 + i * 1000L, 150, 30.0, 4.0, 4.0 * i))
        assertEquals(30, r.size)
        val f = File.createTempFile("sim", ".fit")
        val out = r.write(f, rower = true)
        assertNotNull(out)
        val bytes = f.readBytes()
        assertEquals(".FIT", String(bytes, 8, 4, Charsets.US_ASCII))  // the FIT file header signature (4 bytes at offset 8)
        assertEquals(0, r.size)                                        // the session is cleared once written
        f.delete()
    }

    @Test fun nothingRecordedWritesNothing() {
        val f = File.createTempFile("sim", ".fit")
        assertNull(ActivityRecorder().write(f, rower = false))
        f.delete()
    }
}
