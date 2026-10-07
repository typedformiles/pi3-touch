"""WMO weather codes, as Open-Meteo reports them: a short description and an icon kind.

Standard library only (shared by the Weather and World Clock apps)."""

# WMO weather codes (as used by Open-Meteo) -> (description, icon)
CODES = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "drizzle"), 57: ("Freezing drizzle", "drizzle"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"), 80: ("Light showers", "showers"), 81: ("Showers", "showers"),
    82: ("Heavy showers", "showers"), 85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "thunder"), 96: ("Thunder and hail", "thunder"),
    99: ("Thunder and hail", "thunder"),
}


def describe(code):
    return CODES.get(code, ("", "cloud"))
