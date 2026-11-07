package app.antlogger

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

/**
 * Turns decoded samples into an activity .fit file with 1 Hz records.
 * A value is carried forward for at most [holdMs]; beyond that the field is simply left out of
 * the record, so a sensor dropout shows up as a gap in Garmin Connect rather than as invented data.
 */
object FitExporter {
    class Result(val records: Int, val seconds: Int, val avgHr: Int?, val maxHr: Int?)

    fun export(samples: List<Sample>, out: File, holdMs: Long = 3000): Result? {
        if (samples.isEmpty()) return null
        val sorted = samples.sortedBy { it.tsMs }
        val startMs = sorted.first().tsMs / 1000 * 1000
        val endMs = sorted.last().tsMs / 1000 * 1000

        // one source per metric: the first sensor that reported it (avoids mixing two heart-rate straps)
        val source = HashMap<String, Pair<Int, Int>>()
        for (s in sorted) source.putIfAbsent(s.metric, s.devType to s.devNum)
        val per = HashMap<String, List<Sample>>()
        for ((metric, id) in source) {
            per[metric] = sorted.filter { it.metric == metric && it.devType == id.first && it.devNum == id.second }
        }
        val idx = HashMap<String, Int>() // cursor per metric

        fun valueAt(metric: String, t: Long): Double? {
            val list = per[metric] ?: return null
            var i = idx[metric] ?: -1
            while (i + 1 < list.size && list[i + 1].tsMs <= t) i++
            idx[metric] = i
            if (i < 0) return null
            val s = list[i]
            return if (t - s.tsMs <= holdMs) s.value else null
        }

        fun fitTime(ms: Long) = DateTime(Date(ms))

        val enc = FileEncoder(out, Fit.ProtocolVersion.V2_0)
        try {
            enc.write(FileIdMesg().apply {
                setType(com.garmin.fit.File.ACTIVITY)
                setManufacturer(Manufacturer.DEVELOPMENT)
                setProduct(0)
                setSerialNumber(1L)
                setTimeCreated(fitTime(startMs))
            })
            enc.write(EventMesg().apply {
                setTimestamp(fitTime(startMs))
                setEvent(Event.TIMER)
                setEventType(EventType.START)
            })

            var records = 0
            var hrSum = 0L
            var hrCount = 0
            var hrMax = 0
            var t = startMs
            while (t <= endMs) {
                val rec = RecordMesg()
                rec.setTimestamp(fitTime(t))
                valueAt(Metrics.HEART_RATE, t)?.let {
                    rec.setHeartRate(it.toInt().toShort())
                    hrSum += it.toLong(); hrCount++; if (it.toInt() > hrMax) hrMax = it.toInt()
                }
                valueAt(Metrics.POWER, t)?.let { rec.setPower(it.toInt()) }
                (valueAt(Metrics.CADENCE, t) ?: valueAt(Metrics.CSC_CADENCE, t))?.let { rec.setCadence(it.toInt().toShort()) }
                valueAt(Metrics.SPEED_KMH, t)?.let { rec.setSpeed((it / 3.6).toFloat()) } // FIT speed is m/s
                valueAt(Metrics.DISTANCE_M, t)?.let { rec.setDistance(it.toFloat()) }
                // cycling dynamics: only the fields the FIT format (and so Garmin Connect) knows
                valueAt(Metrics.BALANCE_RIGHT, t)?.let { rec.setLeftRightBalance((0x80 or it.toInt()).toShort()) } // bit 7 = right
                valueAt(Metrics.TE_LEFT, t)?.let { rec.setLeftTorqueEffectiveness(it.toFloat()) }
                valueAt(Metrics.TE_RIGHT, t)?.let { rec.setRightTorqueEffectiveness(it.toFloat()) }
                (valueAt(Metrics.PS_LEFT, t) ?: valueAt(Metrics.PS_COMBINED, t))?.let { rec.setLeftPedalSmoothness(it.toFloat()) }
                (valueAt(Metrics.PS_RIGHT, t) ?: valueAt(Metrics.PS_COMBINED, t))?.let { rec.setRightPedalSmoothness(it.toFloat()) }
                valueAt(Metrics.PCO_LEFT, t)?.let { rec.setLeftPco(it.toInt().toByte()) }
                valueAt(Metrics.PCO_RIGHT, t)?.let { rec.setRightPco(it.toInt().toByte()) }
                for (side in listOf("left", "right")) {
                    val s = valueAt(Metrics.fa(side, "start_deg"), t)
                    val e = valueAt(Metrics.fa(side, "end_deg"), t)
                    if (s != null && e != null) {
                        if (side == "left") { rec.setLeftPowerPhase(0, s.toFloat()); rec.setLeftPowerPhase(1, e.toFloat()) }
                        else { rec.setRightPowerPhase(0, s.toFloat()); rec.setRightPowerPhase(1, e.toFloat()) }
                    }
                    val ps = valueAt(Metrics.fa(side, "peak_start_deg"), t)
                    val pe = valueAt(Metrics.fa(side, "peak_end_deg"), t)
                    if (ps != null && pe != null) {
                        if (side == "left") { rec.setLeftPowerPhasePeak(0, ps.toFloat()); rec.setLeftPowerPhasePeak(1, pe.toFloat()) }
                        else { rec.setRightPowerPhasePeak(0, ps.toFloat()); rec.setRightPowerPhasePeak(1, pe.toFloat()) }
                    }
                }
                enc.write(rec)
                records++
                t += 1000
            }

            val elapsed = ((endMs - startMs) / 1000 + 1).toFloat()
            val avgHr = if (hrCount > 0) (hrSum / hrCount).toInt() else null
            enc.write(EventMesg().apply {
                setTimestamp(fitTime(endMs))
                setEvent(Event.TIMER)
                setEventType(EventType.STOP_ALL)
            })
            enc.write(LapMesg().apply {
                setTimestamp(fitTime(endMs))
                setStartTime(fitTime(startMs))
                setTotalElapsedTime(elapsed)
                setTotalTimerTime(elapsed)
            })
            enc.write(SessionMesg().apply {
                setTimestamp(fitTime(endMs))
                setStartTime(fitTime(startMs))
                setTotalElapsedTime(elapsed)
                setTotalTimerTime(elapsed)
                setSport(Sport.CYCLING)
                setSubSport(SubSport.INDOOR_CYCLING)
                setFirstLapIndex(0)
                setNumLaps(1)
                if (avgHr != null) {
                    setAvgHeartRate(avgHr.toShort())
                    setMaxHeartRate(hrMax.toShort())
                }
            })
            enc.write(ActivityMesg().apply {
                setTimestamp(fitTime(endMs))
                setNumSessions(1)
                setType(Activity.MANUAL)
                setEvent(Event.ACTIVITY)
                setEventType(EventType.STOP)
                setTotalTimerTime(elapsed)
            })
            return Result(records, elapsed.toInt(), avgHr, if (hrCount > 0) hrMax else null)
        } finally {
            enc.close()
        }
    }
}
