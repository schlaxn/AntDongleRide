package app.sensorsim

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Expected values are worked out by hand from the rule in rower/smoothing.py (15 s window, 5 % band, 2 s confirm). */
class SmoothingTest {
    @Test fun firstValueIsTakenAsIs() {
        assertEquals(10.0, AdaptiveSmoother().update(10.0), 0.0)
    }

    @Test fun steadyValueIsAveragedNotFollowedAtOnce() {
        val s = AdaptiveSmoother()
        s.update(10.0)
        // 2 % away: inside the 5 % band, so it is averaged with a 15 s window
        val after1 = s.update(10.2)
        assertEquals(10.0 + 0.2 / 15, after1, 1e-9)
        assertTrue(after1 < 10.05)
    }

    @Test fun sustainedChangeIsFollowedAfterTwoSeconds() {
        val s = AdaptiveSmoother()
        s.update(10.0)
        // 50 % away: outside the band. Not yet after 1 s, followed at 2 s
        assertEquals(10.0, s.update(15.0), 0.0)
        assertEquals(15.0, s.update(15.0), 0.0)
    }

    @Test fun blipIsIgnored() {
        val s = AdaptiveSmoother()
        s.update(10.0)
        s.update(15.0)                     // one step outside the band
        val back = s.update(10.0)          // back inside: the blip is dropped
        assertEquals(10.0, back, 1e-9)
        assertEquals(10.0, s.avg!!, 1e-9)
    }

    @Test fun realSpeedChangeShowsWithinTwoSecondsNotFifteen() {
        val s = AdaptiveSmoother()
        s.update(1.0)
        var t = 0.0
        var seen = -1.0
        // speed jumps to 2.0 m/s and stays there
        while (t < 5.0 && seen < 0) {
            t += 0.25
            val v = s.update(2.0, 0.25)
            if (v > 1.9) seen = t
        }
        assertTrue("took $seen s", seen in 0.0..2.5)
    }
}
