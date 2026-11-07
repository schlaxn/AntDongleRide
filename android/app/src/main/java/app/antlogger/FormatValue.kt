package app.antlogger

/**
 * Formats a display value. The unit is appended AFTER formatting, never placed into the format string:
 * a unit such as " %" contains a bare '%', which String.format reads as a directive and crashes the app
 * (UnknownFormatConversionException, seen on the Live page as soon as a percentage has a value).
 */
fun formatValue(x: Double?, digits: Int = 0, unit: String = ""): String =
    if (x == null) "-" else String.format("%.${digits}f", x) + unit
