package app.sensorsim

import com.garmin.fit.Activity
import com.garmin.fit.ActivityMesg
import com.garmin.fit.DateTime
import com.garmin.fit.Event
import com.garmin.fit.EventMesg
import com.garmin.fit.EventType
import com.garmin.fit.FileEncoder
import com.garmin.fit.FileIdMesg
import com.garmin.fit.Fit
import com.garmin.fit.LapMesg
import com.garmin.fit.Manufacturer
import com.garmin.fit.RecordMesg
import com.garmin.fit.SessionMesg
import com.garmin.fit.Sport
import com.garmin.fit.SubSport
import java.io.File
import java.util.Date
import kotlin.math.roundToInt

/**
 * Records the simulated session once per second and writes it as an activity .fit file (1 Hz records with power, cadence,
 * speed and distance), so the session can be imported into Garmin Connect or read back by a tool.
 */
class ActivityRecorder {
    class Point(val ms: Long, val powerW: Int, val cadence: Double, val speedMps: Double, val distanceM: Double)

    private val points = ArrayList<Point>()

    val size: Int get() = points.size

    fun add(p: Point) {
        points += p
    }

    /** Writes the session to [out] and clears the recorder. Returns the file, or null when nothing was recorded. */
    fun write(out: File, rower: Boolean): File? {
        if (points.isEmpty()) return null
        val startMs = points.first().ms
        val endMs = points.last().ms
        val elapsed = ((endMs - startMs) / 1000 + 1).toFloat()
        out.parentFile?.mkdirs()
        val enc = FileEncoder(out, Fit.ProtocolVersion.V2_0)
        try {
            enc.write(FileIdMesg().apply {
                setType(com.garmin.fit.File.ACTIVITY)
                setManufacturer(Manufacturer.DEVELOPMENT)
                setProduct(0)
                setSerialNumber(1L)
                setTimeCreated(DateTime(Date(startMs)))
            })
            enc.write(EventMesg().apply {
                setTimestamp(DateTime(Date(startMs)))
                setEvent(Event.TIMER)
                setEventType(EventType.START)
            })
            for (p in points) {
                enc.write(RecordMesg().apply {
                    setTimestamp(DateTime(Date(p.ms)))
                    setPower(p.powerW)
                    setCadence(p.cadence.roundToInt().coerceIn(0, 255).toShort())
                    setSpeed(p.speedMps.toFloat())
                    setDistance(p.distanceM.toFloat())
                })
            }
            enc.write(EventMesg().apply {
                setTimestamp(DateTime(Date(endMs)))
                setEvent(Event.TIMER)
                setEventType(EventType.STOP_ALL)
            })
            enc.write(LapMesg().apply {
                setTimestamp(DateTime(Date(endMs)))
                setStartTime(DateTime(Date(startMs)))
                setTotalElapsedTime(elapsed)
                setTotalTimerTime(elapsed)
            })
            enc.write(SessionMesg().apply {
                setTimestamp(DateTime(Date(endMs)))
                setStartTime(DateTime(Date(startMs)))
                setTotalElapsedTime(elapsed)
                setTotalTimerTime(elapsed)
                setSport(if (rower) Sport.ROWING else Sport.CYCLING)
                setSubSport(if (rower) SubSport.INDOOR_ROWING else SubSport.INDOOR_CYCLING)
                setFirstLapIndex(0)
                setNumLaps(1)
            })
            enc.write(ActivityMesg().apply {
                setTimestamp(DateTime(Date(endMs)))
                setNumSessions(1)
                setType(Activity.MANUAL)
                setEvent(Event.ACTIVITY)
                setEventType(EventType.STOP)
                setTotalTimerTime(elapsed)
            })
        } finally {
            enc.close()
            points.clear()
        }
        return out
    }
}
