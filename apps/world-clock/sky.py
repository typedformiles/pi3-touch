"""The sums behind the World Clock faces (no pygame here): where the sun is, what colour the
sky is, and how a city's time compares with home.

Sun position uses the standard low-precision almanac formulas (good to about a degree,
plenty for colouring a clock face).
"""
import math
from datetime import datetime, timezone

UTC = timezone.utc
J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)

# Sky colour by the sun's elevation (degrees): night, twilight, dawn/dusk glow, golden hour, day
# (orange blended into blue turns grey, so golden hour hands over to blue quickly)
SKY = [(-18, (12, 16, 38)), (-12, (20, 28, 62)), (-6, (52, 44, 96)), (-2, (150, 82, 110)),
       (1, (224, 128, 86)), (5, (240, 176, 104)), (9, (150, 188, 228)), (16, (110, 168, 230)),
       (40, (82, 150, 228))]


def sun_elevation(lat, lon, when):
    """Elevation of the sun above the horizon in degrees, at an aware datetime."""
    n = (when - J2000).total_seconds() / 86400
    mean_long = (280.460 + 0.9856474 * n) % 360
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360)
    ecl_long = math.radians(mean_long + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    obliquity = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(obliquity) * math.sin(ecl_long), math.cos(ecl_long))
    dec = math.asin(math.sin(obliquity) * math.sin(ecl_long))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    hour_angle = math.radians(gmst * 15 + lon) - ra
    lat = math.radians(lat)
    return math.degrees(math.asin(math.sin(lat) * math.sin(dec) +
                                  math.cos(lat) * math.cos(dec) * math.cos(hour_angle)))


def mix(a, b, f):
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


def sky_colour(elevation):
    """Face colour for a sun elevation, blended between the SKY keyframes."""
    if elevation <= SKY[0][0]:
        return SKY[0][1]
    for (e0, c0), (e1, c1) in zip(SKY, SKY[1:]):
        if elevation <= e1:
            return mix(c0, c1, (elevation - e0) / (e1 - e0))
    return SKY[-1][1]


def luminance(rgb):
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_light(rgb):
    """Light faces get dark hands and ticks; dark ones get light."""
    return luminance(rgb) > 0.5


def offset_label(when, tz, home):
    """"+8h", "−5h", "+5h30" relative to home (or "" for the same time)."""
    mins = round(((when.astimezone(tz).utcoffset() - when.astimezone(home).utcoffset()).total_seconds()) / 60)
    if mins == 0:
        return ""
    sign = "+" if mins > 0 else "−"
    h, m = divmod(abs(mins), 60)
    return f"{sign}{h}h" + (f"{m:02d}" if m else "")


def day_label(when, tz, home):
    """"tomorrow" / "yesterday" when the city's date differs from home's, else ""."""
    diff = (when.astimezone(tz).date() - when.astimezone(home).date()).days
    return {1: "tomorrow", -1: "yesterday"}.get(diff, "")
