"""Jarvis's look, shared by the widget and the screen overlay: iOS dark mode, one colour per state: colours, type, radii, shadows, motion."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPen

# One colour per state, never mixed: no multicolour gradients anywhere; glows vary only in brightness.
# Swap any of these freely.
COLORS = {
    "listening": "#0A84FF",     # iOS blue
    "thinking": "#F2F2F7",      # soft white
    "speaking": "#BF5AF2",      # iOS purple
    "waiting": "#FF9F0A",       # iOS orange: waiting for your yes or no
    "ready": "#30D158",         # iOS green: an update is waiting for a quiet moment on your call
    "background": "#F2F2F7",    # background-work notes
    "idle": "#8E8E93",
    "offline": "#8E8E93",
    "annotate": "#0A84FF",      # the overlay's rings, arrows, boxes and step badges
}
INK = QColor(12, 12, 14)                     # text on light fills


def color(name):
    return QColor(COLORS.get(name, COLORS["idle"]))


def on(c):
    """Readable text colour on a fill of colour c."""
    return INK if c.lightnessF() > 0.7 else LABEL


GREY = QColor(142, 142, 147)
LABEL = QColor(255, 255, 255)
SECONDARY = QColor(235, 235, 245, 153)       # iOS secondaryLabel (60%)
TERTIARY = QColor(235, 235, 245, 76)         # iOS tertiaryLabel (30%)
GLASS = QColor(28, 28, 30, 217)              # callout pills
ISLAND = QColor(10, 10, 12, 190)             # the widget capsule, Dynamic Island black
HAIRLINE = QColor(255, 255, 255, 34)

RADIUS_PILL = 12
RADIUS_ISLAND = 26
SHADOW = (16, 6, 70)                         # blur, y offset, peak alpha

# Motion (seconds)
FADE_IN = 0.22
FADE_OUT = 0.20
STAGGER = 0.12
SPRING = (190.0, 19.0)                       # stiffness, damping: settles in ~0.35 s with a hint of bounce

FAMILIES = ["Inter", "SF Pro Text", "Noto Sans"]   # first one installed wins


def font(px, weight=QFont.Normal):
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setHintingPreference(QFont.PreferNoHinting)   # smooth, Apple-like glyphs
    return f


def alpha(color, a):
    """The colour with its alpha multiplied by a (0-1)."""
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * a)))
    return c


def clamp01(t):
    return max(0.0, min(1.0, t))


def ease_out(t):
    return 1 - (1 - clamp01(t)) ** 3


def ease_back(t):
    """Ease out with a small overshoot, for things that pop in."""
    t = clamp01(t) - 1
    return 1 + 2.2 * t ** 3 + 1.2 * t ** 2


def shadow(p, path, opacity=1.0, blur=SHADOW[0], dy=SHADOW[1], peak=SHADOW[2]):
    """Soft drop shadow under a filled path: stacked translucent outlines approximate a gaussian blur."""
    p.save()
    p.translate(0, dy)
    p.setBrush(Qt.NoBrush)
    steps = 8
    for i in range(steps, 0, -1):
        p.setPen(QPen(QColor(0, 0, 0, round(peak * opacity / steps * 0.9)), blur * i / steps,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    p.fillPath(path, QColor(0, 0, 0, round(peak * opacity * 0.5)))
    p.restore()


class Spring:
    """A damped spring toward a target: iOS-style motion for sizes and positions."""
    def __init__(self, value):
        self.value, self.target, self.velocity = float(value), float(value), 0.0

    def step(self, dt):
        k, c = SPRING
        for _ in range(4):                    # small sub-steps keep it stable at 30-60 fps
            h = dt / 4
            self.velocity += ((self.target - self.value) * k - self.velocity * c) * h
            self.value += self.velocity * h
        if abs(self.target - self.value) < 0.05 and abs(self.velocity) < 0.05:
            self.value, self.velocity = self.target, 0.0
        return self.value

    @property
    def moving(self):
        return self.value != self.target or self.velocity != 0.0
