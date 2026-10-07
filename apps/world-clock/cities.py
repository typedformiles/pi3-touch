"""The World Clock's cities, in screen order (2 columns x 3 rows), and what each city page uses.

name, tz          - shown name and time zone
lat, lon          - city centre (sky colour, weather, air quality)
sea               - a point just offshore for sea temperature, or None
currency          - ISO code; "£1 = ..." on the Money page
holidays          - (Nager.Date country, region or None) for public holidays, or None if not covered
weekend           - shown instead of holidays when there's no holiday data
exchange          - stock exchange: name, time zone, trading sessions (local "HH:MM" pairs)
news              - (source name, RSS feed URL)
"""

CITIES = [
    {"name": "San Francisco", "tz": "America/Los_Angeles", "lat": 37.77, "lon": -122.42,
     "sea": (37.80, -122.55), "currency": "USD", "holidays": ("US", "US-CA"),
     "exchange": {"name": "NYSE", "tz": "America/New_York", "sessions": [("09:30", "16:00")]},
     "news": ("KQED", "https://ww2.kqed.org/news/feed/")},
    {"name": "New York", "tz": "America/New_York", "lat": 40.71, "lon": -74.01,
     "sea": (40.55, -73.90), "currency": "USD", "holidays": ("US", "US-NY"),
     "exchange": {"name": "NYSE", "tz": "America/New_York", "sessions": [("09:30", "16:00")]},
     "news": ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/NYRegion.xml")},
    {"name": "London", "tz": "Europe/London", "lat": 51.51, "lon": -0.13,
     "sea": None, "currency": "GBP", "holidays": ("GB", "GB-ENG"),
     "exchange": {"name": "LSE", "tz": "Europe/London", "sessions": [("08:00", "16:30")]},
     "news": ("BBC London", "https://feeds.bbci.co.uk/news/england/london/rss.xml")},
    {"name": "Dubai", "tz": "Asia/Dubai", "lat": 25.20, "lon": 55.27,
     "sea": (25.25, 55.20), "currency": "AED", "holidays": None, "weekend": "Saturday and Sunday",
     "exchange": {"name": "DFM", "tz": "Asia/Dubai", "sessions": [("10:00", "15:00")]},
     "news": ("The National", "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml")},
    {"name": "Tokyo", "tz": "Asia/Tokyo", "lat": 35.68, "lon": 139.69,
     "sea": (35.50, 139.85), "currency": "JPY", "holidays": ("JP", None),
     "exchange": {"name": "TSE", "tz": "Asia/Tokyo", "sessions": [("09:00", "11:30"), ("12:30", "15:30")]},
     "news": ("Japan Times", "https://www.japantimes.co.jp/feed/")},
    {"name": "Sydney", "tz": "Australia/Sydney", "lat": -33.87, "lon": 151.21,
     "sea": (-33.89, 151.28), "currency": "AUD", "holidays": ("AU", "AU-NSW"),
     "exchange": {"name": "ASX", "tz": "Australia/Sydney", "sessions": [("10:00", "16:00")]},
     "news": ("Sydney Morning Herald", "https://www.smh.com.au/rss/feed.xml")},
]
