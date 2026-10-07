"""The HyperPixel for pygame apps (Weather, World Clock, Moode Remote): open it, show frames,
read touches - turned to match how the panel is mounted (pitouch.rotation()).

    display = Display(480, 800, "Weather")
    draw on display.surface ... then display.present()
    display.point(event) -> (x, y) of a touch/mouse event, in upright screen coordinates

Upside down (rotate 180), apps draw upright on an off-screen surface and present() turns
each frame over on its way to the panel - only when a frame is shown, so it costs a few
milliseconds per redraw.
"""
import pygame

import pitouch


class Display:
    def __init__(self, w, h, caption):
        # Display and fonts only: pygame.init() would also start audio, whose threads
        # cost ~5% CPU on a Pi 3 playing silence
        pygame.display.init()
        pygame.font.init()
        pygame.mouse.set_visible(False)
        # KMS/DRM fullscreen - on the HyperPixel even when an HDMI monitor is also connected
        flags = pygame.FULLSCREEN | pygame.NOFRAME
        sizes = pygame.display.get_desktop_sizes()
        index = next((i for i, sz in enumerate(sizes) if sz == (w, h)), 0)
        self.window = pygame.display.set_mode((w, h), flags, display=index)
        pygame.display.set_caption(caption)
        self.w, self.h = w, h
        self.rotate = pitouch.rotation()
        self.surface = pygame.Surface((w, h)).convert() if self.rotate else self.window

    def present(self):
        if self.rotate:
            self.window.blit(pygame.transform.flip(self.surface, True, True), (0, 0))
        pygame.display.flip()

    def point(self, event):
        if event.type in (pygame.FINGERDOWN, pygame.FINGERUP):
            x, y = event.x * self.w, event.y * self.h
        else:
            x, y = event.pos
        if self.rotate == 180:
            x, y = self.w - 1 - x, self.h - 1 - y
        return int(x), int(y)
