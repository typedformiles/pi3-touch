"""Colours, fonts and drawing helpers shared by Swifty's games (pygame)."""
import math
import os

import pygame

SCREEN_W, SCREEN_H = 480, 800
PAD = 16
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
SERIF_DIR = os.environ.get("PI3_SERIF_DIR", "/usr/share/fonts/truetype/freefont")

# ── Colours: midnight purple, with the era's colour once it's known ─────────────
BG          = (16,  11,  32)
BG_CARD     = (32,  24,  56)
BG_CARD_HI  = (52,  42,  86)
TEXT_PRI    = (248, 242, 252)
TEXT_SEC    = (180, 166, 206)
TEXT_DIM    = (112, 98,  142)
ACCENT      = (222, 176, 255)   # lilac
GOLD        = (255, 214, 120)
RIGHT       = (84,  196, 128)
WRONG       = (232, 84,  100)
BEADS       = [(255, 150, 190), (255, 186, 130), (255, 226, 120), (150, 226, 170), (140, 200, 255),
               (200, 160, 255)]  # a friendship bracelet


def mix(a, b, t):
    """a moved t of the way to b."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def luminance(colour):
    """Relative luminance, as the WCAG contrast rules work it out."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ink_on(colour):
    """Dark or light text, whichever stands out more on colour."""
    return BG if contrast(colour, BG) > contrast(colour, TEXT_PRI) else TEXT_PRI


def load_font(size, bold=False):
    path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return pygame.font.Font(path, size)
    except (OSError, FileNotFoundError):
        return pygame.font.SysFont("dejavusans,freesans,arial", size, bold=bold)


def load_serif(size, bold=False, italic=False):
    """GNU FreeSerif (it has italics), else DejaVu Serif, else whatever pygame has."""
    name = "FreeSerif" + ("Bold" if bold else "") + ("Italic" if italic else "") + ".ttf"
    for path in (os.path.join(SERIF_DIR, name),
                 os.path.join(FONT_DIR, "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf")):
        try:
            return pygame.font.Font(path, size)
        except (OSError, FileNotFoundError):
            continue
    return pygame.font.SysFont("serif", size, bold=bold, italic=italic)


def truncate(text, font, max_width):
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


def wrap(text, font, width):
    """Split text into lines no wider than width (a word too long for a line is cut)."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.size(trial)[0] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    lines += [line] if line else []
    return [truncate(ln, font, width) for ln in lines]


class Ui:
    """Drawing on the screen surface; buttons drawn this frame are remembered for taps."""

    def __init__(self, screen):
        self.screen = screen
        self.fonts = {}
        self.buttons = {}           # name -> Rect, rebuilt every frame

    def font(self, size, bold=False):
        if ("sans", size, bold) not in self.fonts:
            self.fonts[("sans", size, bold)] = load_font(size, bold)
        return self.fonts[("sans", size, bold)]

    def serif(self, size, bold=False, italic=False):
        if ("serif", size, bold, italic) not in self.fonts:
            self.fonts[("serif", size, bold, italic)] = load_serif(size, bold, italic)
        return self.fonts[("serif", size, bold, italic)]

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14, width=0):
        pygame.draw.rect(self.screen, colour, rect, width, border_radius=radius)

    def button(self, name, rect, label, fill, ink, font=None, radius=14):
        self.card(rect, fill, radius=radius)
        self.text(label, font or self.font(20, True), ink, rect.center, "center")
        self.buttons[name] = rect

    def shrink(self, s, width, sizes, bold=True, serif=False, italic=False):
        """The biggest font of sizes that fits s in width (the smallest, cut, if none)."""
        for size in sizes:
            f = self.serif(size, bold, italic) if serif else self.font(size, bold)
            if f.size(s)[0] <= width:
                return f, s
        return f, truncate(s, f, width)

    def star(self, colour, centre, r):
        """A four-pointed sparkle."""
        x, y = centre
        pts = []
        for k in range(8):
            a = math.pi / 4 * k - math.pi / 2
            rr = r if k % 2 == 0 else r * 0.32
            pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
        pygame.draw.polygon(self.screen, colour, pts)
