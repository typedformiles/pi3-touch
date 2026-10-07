"""Weather icons for pygame apps (Weather, World Clock), drawn with shapes - no image files.

draw_icon(surface, wmo_code, is_day, cx, cy, size, bg) - bg is the colour behind the icon
(the night crescent is cut out of a disc with it).
"""
import math

import pygame

from wmo import describe

SUN        = (250, 196, 64)
MOON       = (220, 226, 240)
CLOUD      = (176, 188, 204)
CLOUD_DARK = (120, 132, 150)
RAIN       = (96,  170, 240)
SNOW       = (238, 242, 248)


def draw_cloud(surf, cx, cy, s, colour):
    pygame.draw.circle(surf, colour, (int(cx - s * 0.22), int(cy + s * 0.04)), int(s * 0.2))
    pygame.draw.circle(surf, colour, (int(cx + s * 0.05), int(cy - s * 0.08)), int(s * 0.27))
    pygame.draw.circle(surf, colour, (int(cx + s * 0.28), int(cy + s * 0.06)), int(s * 0.18))
    pygame.draw.rect(surf, colour, (cx - s * 0.22, cy + s * 0.04, s * 0.5, s * 0.2))


def draw_sun(surf, cx, cy, s, is_day, bg):
    r = s * 0.2
    if not is_day:                                   # crescent moon
        pygame.draw.circle(surf, MOON, (int(cx), int(cy)), int(r * 1.2))
        pygame.draw.circle(surf, bg, (int(cx + r * 0.6), int(cy - r * 0.4)), int(r * 1.05))
        return
    for i in range(8):
        a = math.radians(i * 45)
        pygame.draw.line(surf, SUN, (cx + math.cos(a) * r * 1.45, cy + math.sin(a) * r * 1.45),
                         (cx + math.cos(a) * r * 2.0, cy + math.sin(a) * r * 2.0), max(2, int(s / 30)))
    pygame.draw.circle(surf, SUN, (int(cx), int(cy)), int(r))


def draw_icon(surf, code, is_day, cx, cy, s, bg):
    kind = describe(code)[1]
    if kind == "clear":
        draw_sun(surf, cx, cy, s * 1.2, is_day, bg)
        return
    if kind in ("partly", "showers"):
        draw_sun(surf, cx - s * 0.18, cy - s * 0.2, s * 0.9, is_day, bg)
    dark = kind in ("rain", "thunder", "drizzle", "snow")
    draw_cloud(surf, cx, cy - s * 0.05, s, CLOUD_DARK if dark else CLOUD)
    w = max(2, int(s / 28))
    if kind in ("rain", "showers", "drizzle"):
        n = 2 if kind == "drizzle" else 3
        for i in range(n):
            x = cx - s * 0.16 + i * s * 0.17
            pygame.draw.line(surf, RAIN, (x, cy + s * 0.28), (x - s * 0.07, cy + s * 0.45), w)
    elif kind == "snow":
        for i in range(3):
            pygame.draw.circle(surf, SNOW, (int(cx - s * 0.16 + i * s * 0.17), int(cy + s * 0.37)), max(2, int(s / 22)))
    elif kind == "thunder":
        x, y = cx, cy + s * 0.22
        pygame.draw.polygon(surf, SUN, [(x, y), (x - s * 0.1, y + s * 0.16), (x, y + s * 0.16),
                                        (x - s * 0.06, y + s * 0.3), (x + s * 0.1, y + s * 0.1),
                                        (x + s * 0.01, y + s * 0.1), (x + s * 0.06, y)])
    elif kind == "fog":
        for i in range(3):
            y = cy + s * 0.26 + i * s * 0.1
            pygame.draw.line(surf, CLOUD, (cx - s * 0.3, y), (cx + s * 0.3, y), w)
