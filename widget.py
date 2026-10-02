"""Jarvis desktop widget: a Dynamic Island style capsule, always on top and click-through, bottom centre of a monitor.

Resting, it's a small dark pill with a dim mic (a collapsed Dynamic Island). It springs open when you start talking
(wake word or hotkey), stays open while listening, thinking and speaking, and settles back into the pill a couple of
seconds after Jarvis finishes. Background-work changes ("+1 in the background") and "Jarvis isn't running" open it for
a few seconds too. One colour per state (design.COLORS), never mixed: listening, a mic that glows with your voice;
thinking, a softly turning orb and a shimmer across the label; speaking, the capsule's whole edge glows (iOS 18 Siri
style) as bright as Jarvis's voice; waiting for yes/no, an orange orb; an update held until your call goes quiet, a
small "Update ready" pill with a green orb.
The text: what you're saying (live), then what he's saying, sentence by sentence. Look: design.py.

Run: systemctl --user start jarvis-widget   (starts and stops with Jarvis)
"""
import http.client
import json
import math
import os
import sys
import threading
import time

SYSTEM_PY = "/usr/bin/python3"
if __name__ == "__main__" and sys.executable != SYSTEM_PY:   # layer-shell needs the system Qt, not the venv's
    os.execv(SYSTEM_PY, [SYSTEM_PY, os.path.abspath(__file__)])  # (see overlay.py); before any Qt loads

from PySide6.QtCore import QMargins, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient, QRegion)
from PySide6.QtWidgets import QApplication, QWidget

import config
import design as d
import overlay

TITLE = "Jarvis Widget"
CAP_W = 600                                   # expanded capsule width
CAP_MAX_H = 132
PAD_X, PAD_TOP, PAD_BOTTOM = 22, 20, 26       # room around the capsule for its shadow and edge glow
W, H = CAP_W + 2 * PAD_X, CAP_MAX_H + PAD_TOP + PAD_BOTTOM
PORT = 8765
ACTIVE = ("listening", "thinking", "speaking", "waiting", "ready")   # open while these last; a small pill otherwise
REST_W, REST_H = 112, 32                      # the resting pill (a collapsed Dynamic Island)
LINGER_S = 2.5                                # stays up this long after he finishes, then tucks away
NOTE_S = 4.0                                  # background-work / offline notes show this long

LABELS = {"idle": "Idle", "listening": "Listening", "thinking": "Thinking", "speaking": "Jarvis",
          "waiting": "Say yes or no", "ready": "Update ready", "offline": "Jarvis isn't running"}
TOOL_NAMES = {"screenshot": "looking at the screen", "click_at": "clicking", "move_mouse": "moving the mouse",
              "scroll": "scrolling", "type_text": "typing", "press_keys": "pressing keys", "open_app": "opening an app",
              "focus_window": "switching windows", "list_windows": "checking windows", "media": "media controls",
              "volume": "volume", "Bash": "running a command", "Read": "reading a file", "Write": "writing a file",
              "Edit": "editing a file", "Grep": "searching files", "Glob": "finding files", "WebSearch": "searching the web",
              "WebFetch": "reading a web page", "ToolSearch": "loading tools", "Agent": "sub-agent working",
              "navigate": "opening a page in Chrome", "computer": "working in Chrome", "read_page": "reading the page",
              "get_page_text": "reading the page", "find": "searching the page", "form_input": "filling a form",
              "annotate": "pointing on screen"}


def short_dur(sec):
    sec = int(max(0, sec))
    return f"{sec}s" if sec < 60 else f"{sec // 60}m {sec % 60:02d}s" if sec < 3600 else f"{sec // 3600}h {sec % 3600 // 60:02d}m"


def describe_step(ev):
    """'Command: Check the weather' rather than just 'running a command'."""
    short = ev["name"].split("__")[-1]
    name = TOOL_NAMES.get(short, short.replace("_", " "))
    try:
        inp = json.loads(ev.get("input") or "{}")
    except ValueError:
        inp = {}
    detail = inp.get("description") or inp.get("query") or inp.get("url") or inp.get("name") or ""
    if short in ("Read", "Write", "Edit") and inp.get("file_path"):
        detail = os.path.basename(inp["file_path"])
    if short in ("ToolSearch", "screenshot", "click_at", "move_mouse", "scroll", "annotate"):
        detail = ""
    name = name[0].upper() + name[1:]
    return f"{name}: {detail}" if detail else name


class Feed(QObject):
    """Asks Jarvis what's happening several times a second (a tiny local request, about 2 ms)."""
    event = Signal(dict)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _get(path):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{PORT}"})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body

    def _run(self):
        use_live = True
        while True:
            busy = False
            try:
                if use_live:
                    status, body = self._get("/api/live")
                    if status == 404:
                        use_live = False       # an older Jarvis without /api/live: use the full status instead
                        continue
                    view = json.loads(body)
                else:
                    status, body = self._get("/api/snapshot")
                    view = from_snapshot(json.loads(body))
                self.event.emit({"kind": "live", **view})
                busy = view["activity"] != "idle" or bool(view["tasks"])
            except Exception:
                self.event.emit({"kind": "live", "activity": "offline", "mic": 0, "out": 0, "tasks": [],
                                 "question": "", "you": "", "said": "", "step": "", "self_started": False})
                use_live = True
                time.sleep(2)
                continue
            time.sleep((1 / 15 if use_live else 0.7) if busy else 0.5)


def from_snapshot(snap):
    """Build the widget's view from the dashboard snapshot (fallback for an older running Jarvis)."""
    hist = snap.get("history", [])
    last_turn = max((i for i, e in enumerate(hist) if e["kind"] == "turn_start"), default=-1)
    said = step = ""
    for e in hist[last_turn + 1:]:
        if e["kind"] == "say":
            said = e["text"]
        elif e["kind"] == "tool_use" and not e.get("sub"):
            step = describe_step(e)
    heard = [e for e in hist if e["kind"] == "heard" and e.get("text")]
    turn = snap.get("turn") or {}
    return {"activity": snap.get("activity", "idle"), "mic": 0, "out": 0, "question": snap.get("question") or "",
            "tasks": snap.get("tasks", []), "you": (heard[-1]["text"] if heard else "") if snap.get("question")
            else turn.get("text") or (heard[-1]["text"] if heard else ""), "said": said, "step": step,
            "self_started": turn.get("source") == "self"}


def screen_and_margins():
    """The monitor from config, and where on it: layer-shell anchors plus margins that put the capsule's outer edge
    WIDGET_MARGIN_* from the screen edge (the window is a little bigger, for the shadow and the edge glow)."""
    screens = sorted(QApplication.screens(), key=lambda s: s.geometry().x())
    pick = {"left": screens[0], "right": screens[-1]}.get(config.WIDGET_SCREEN) or next(
        (s for s in screens if s.name() == config.WIDGET_SCREEN), screens[0])
    corner = config.WIDGET_CORNER
    anchors = (overlay.TOP if corner.startswith("top") else overlay.BOTTOM) | \
        (overlay.RIGHT if corner.endswith("right") else overlay.LEFT if corner.endswith("left") else 0)
    mx, my = config.WIDGET_MARGIN_X - PAD_X, config.WIDGET_MARGIN_Y
    margins = QMargins(mx, my - PAD_TOP, mx, my - PAD_BOTTOM)       # left, top, right, bottom
    return pick, anchors, margins


class Widget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self.activity = "offline"
        self.mic = self.out = 0.0         # smoothed levels
        self.mic_raw = self.out_raw = 0.0
        self.you = ""                     # what you said (live, then final)
        self.said = ""                    # sentence he's speaking now
        self.step = ""
        self.question = ""
        self.tasks = {}                   # background work: task_id -> {description, started, last_tool}
        self.self_started = False
        self.t0 = self.last_tick = time.time()
        self.linger_until = 0.0           # stays up this long after he finishes
        self.note_until = 0.0             # a background-work or offline note shows until then
        self.task_ids = frozenset()
        self.font_label = d.font(12, QFont.DemiBold)
        self.font_text = d.font(15)
        self.font_small = d.font(13)
        self.font_pill = d.font(14, QFont.Medium)
        self.cap_w, self.cap_h = d.Spring(CAP_W), d.Spring(64)
        self.presence = d.Spring(0)       # 0 = the small resting pill, 100 = fully open (springs both ways)
        self.content = None               # what's on show: (label, text, dim, footer, lines, compact)
        self.content_key = None
        self.content_t0 = 0.0             # when the current kind of content appeared (for its fade-in)
        self.last_key = None
        self.glass_key = self.glass_pm = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    # ---------- events ----------

    def on_event(self, ev):
        if ev.get("kind") != "live":
            return
        if ev["activity"] != self.activity:
            if ev["activity"] == "offline":
                self.note_until = time.time() + NOTE_S
            self.activity = ev["activity"]
        self.mic_raw, self.out_raw = ev.get("mic", 0), ev.get("out", 0)
        self.question = ev.get("question", "")
        self.you, self.said, self.step = ev.get("you", ""), ev.get("said", ""), ev.get("step", "")
        self.self_started = ev.get("self_started", False)
        self.tasks = {t.get("task_id", str(i)): t for i, t in enumerate(ev.get("tasks", []))}
        ids = frozenset(self.tasks)
        if ids != self.task_ids:
            self.task_ids = ids
            if ids:                        # a job started or finished and others are still going: say so briefly
                self.note_until = time.time() + NOTE_S

    def compose(self, now):
        """What to show: (label, text, dim, footer, task_lines, compact). Same wording as always."""
        a = self.activity
        label, text, dim, footer, lines = LABELS.get(a, a), "", False, "", None
        if self.tasks and a != "idle" and now < self.note_until:
            footer = f"+{len(self.tasks)} in the background"
        if a == "listening":
            label, text = "You", self.you or "..."
        elif a == "thinking":
            label = "Working by himself" if self.self_started else "Thinking"
            if self.step:
                text = self.step
            else:
                text, dim = ("Checking on a background job" if self.self_started else self.you), True
        elif a == "speaking":
            text = self.said or "..."
        elif a == "waiting":
            text = self.question
        elif a == "idle" and self.said and now < self.linger_until:
            label, text, dim = "Jarvis", self.said, True
        elif a == "idle" and self.tasks:
            label = f"Working in the background ({len(self.tasks)})"
            lines = [(short_dur(now - t["started"]), t["description"] or "Background job")
                     for t in sorted(self.tasks.values(), key=lambda t: t["started"])[:3]]
        elif a == "idle":
            text, dim = "Say “Hey Jarvis”", True
        elif a == "offline":
            text, dim = "Jarvis isn't running", True
        elif a == "ready":                    # an update held for a quiet moment on your call: a small green pill
            text = "Update ready"
        compact = a in ("idle", "offline", "ready") and not lines and label != "Jarvis"
        return label, text, dim, footer, lines, compact

    def tick(self):
        now = time.time()
        dt, self.last_tick = min(0.05, now - self.last_tick), now
        # attack fast, release slow, so the glow feels alive rather than jittery
        for name in ("mic", "out"):
            raw, cur = getattr(self, name + "_raw"), getattr(self, name)
            setattr(self, name, cur + (raw - cur) * (0.5 if raw > cur else 0.12))
        if self.activity in ACTIVE:
            self.linger_until = now + LINGER_S
        show = self.activity in ACTIVE or now < self.linger_until or now < self.note_until
        self.presence.target = 100 if show else 0
        if show or self.content is None:   # while tucking away, keep showing what was there
            self.content = self.compose(now)
        key = (self.content[0], self.content[5])
        if key != self.content_key:
            self.content_key, self.content_t0 = key, now
        self.cap_w.target, self.cap_h.target = self.target_size(self.content)
        for spring in (self.cap_w, self.cap_h, self.presence):
            spring.step(dt)
        if not show and not self.presence.moving:      # resting pill: still, so only redraw if it changes
            key = ("rest", self.activity == "offline", bool(self.tasks))
            if key != self.last_key:
                self.last_key = key
                self.update()
            self.timer.setInterval(100)
            return
        # Full 60 fps only while it morphs or follows a voice; the orb alone (thinking, waiting) repaints just its
        # own corner at 30 fps; a note that just sits there only repaints when its text changes (saves CPU).
        morphing = (self.cap_w.moving or self.cap_h.moving or self.presence.moving or now - self.content_t0 < 0.4)
        voice = self.activity in ("listening", "speaking")
        orb_only = not (morphing or voice) and (self.activity in ("thinking", "waiting", "ready") or bool(self.content[4]))
        key = (self.content, int(now // 30))
        if morphing or voice or key != self.last_key:
            self.last_key = key
            self.update()
        elif orb_only:
            cap = self.capsule()
            self.update(QRectF(cap.left() - 4, cap.top(), 66, min(cap.height(), 64)).toAlignedRect())
            if self.activity == "thinking":                     # the shimmering label
                self.update(QRectF(cap.left(), cap.top(), cap.width(), 34).toAlignedRect())
        self.timer.setInterval(16 if morphing or voice else 33 if orb_only else 100)

    # ---------- layout ----------

    def bare(self, content):
        """Speaking (and the moment after) has no icon: the capsule's glowing edge is the voice."""
        return self.activity == "speaking" or (self.activity == "idle" and content[0] == "Jarvis")

    def target_size(self, content):
        label, text, dim, footer, lines, compact = content
        if compact:
            fm = QFontMetricsF(self.font_pill)
            return min(CAP_W, 44 + fm.horizontalAdvance(text) + 20), 40
        body = (len(lines) * 20) if lines else self.text_height(text, self.bare(content))
        h = 14 + 16 + 4 + body + (20 if footer else 0) + 14
        return CAP_W, max(64, min(CAP_MAX_H, h))

    def text_height(self, text, bare):
        if not text:
            return 0
        fm = QFontMetricsF(self.font_text)
        r = fm.boundingRect(QRectF(0, 0, CAP_W - (44 if bare else 80), 1000), Qt.TextWordWrap, text)
        return min(r.height(), 3 * fm.lineSpacing())

    def capsule(self):
        pr = max(0.0, self.presence.value / 100)
        w = REST_W + (self.cap_w.value - REST_W) * pr       # opens out of the resting pill, and settles back into it
        h = REST_H + (self.cap_h.value - REST_H) * pr
        corner = config.WIDGET_CORNER
        x = W - PAD_X - w if corner.endswith("right") else PAD_X if corner.endswith("left") else (W - w) / 2
        y = PAD_TOP if corner.startswith("top") else H - PAD_BOTTOM - h
        return QRectF(x, y, w, h)

    # ---------- drawing ----------

    def paintEvent(self, _):
        if not self.content:
            if self.glass_key is not None:                   # nothing drawn: drop the blur with it
                self.glass_key = None
                self.blur_region = QRegion()                 # held on self: ctypes only gets a raw pointer
                overlay._blur(overlay._ptr(self.windowHandle()), False, overlay._ptr(self.blur_region))
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        cap = self.capsule()
        radius = min(d.RADIUS_ISLAND, cap.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(cap, radius, radius)
        p.drawPixmap(0, 0, self.glass(cap, path))
        if self.activity == "speaking":
            self.draw_edge_glow(p, cap, path)
        if self.presence.value < 40:                         # resting (or nearly): just a dim mic
            self.draw_rest(p, cap, d.clamp01(1 - self.presence.value / 40))
        if self.presence.value < 60:                         # too small for words yet
            return
        p.save()
        p.setClipPath(path)
        p.setOpacity(p.opacity() * d.ease_out((time.time() - self.content_t0) / 0.28)
                     * d.clamp01((self.presence.value - 60) / 30))
        label, text, dim, footer, lines, compact = self.content
        if compact:
            self.draw_compact(p, cap, text)
        else:
            self.draw_expanded(p, cap, label, text, dim, footer, lines)
        p.restore()

    def glass(self, cap, path):
        """The capsule itself (clear frosted glass, sheen, hairline), redrawn only when its size changes."""
        key = (round(cap.x(), 1), round(cap.y(), 1), round(cap.width(), 1), round(cap.height(), 1))
        if key != self.glass_key:
            dpr = self.devicePixelRatioF()
            pm = QPixmap(round(W * dpr), round(H * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            sheen = QLinearGradient(cap.topLeft(), QPointF(cap.left(), cap.top() + min(40, cap.height())))
            sheen.setColorAt(0, QColor(255, 255, 255, 14))
            sheen.setColorAt(1, QColor(255, 255, 255, 0))
            q.fillPath(path, sheen)
            q.setPen(QPen(d.HAIRLINE, 1))
            q.drawPath(path)
            q.end()
            self.glass_key, self.glass_pm = key, pm
            # Frosted glass: KWin blurs what's behind the capsule, same call as the overlay pills.
            self.blur_region = QRegion(path.toFillPolygon().toPolygon())   # held on self: ctypes only gets a raw pointer
            overlay._blur(overlay._ptr(self.windowHandle()), True, overlay._ptr(self.blur_region))
        return self.glass_pm

    def draw_edge_glow(self, p, cap, path):
        """iOS 18 Siri style: soft light running round the whole edge, as bright as Jarvis's voice is loud."""
        t = time.time() - self.t0
        c = d.color("speaking")
        g = QConicalGradient(cap.center(), -t * 110)      # one colour; two brighter arcs chase round the edge
        for pos, a in ((0, 1.0), (0.18, 0.25), (0.5, 0.85), (0.68, 0.2), (1, 1.0)):
            g.setColorAt(pos, d.alpha(c, a))
        level = 0.35 + 0.65 * self.out
        base = p.opacity()
        p.setBrush(Qt.NoBrush)
        for width, a in ((14, 0.12), (8, 0.25), (3.5, 0.55), (1.5, 0.95)):   # wide and faint to thin and bright
            p.setOpacity(base * a * level)
            p.setPen(QPen(g, width))
            p.drawPath(path)
        p.setOpacity(base)

    def draw_rest(self, p, cap, k):
        """The resting pill: a tiny dim mic in the middle, plus a faint dot while background work runs."""
        c = cap.center()
        col = d.alpha(d.LABEL, (0.22 if self.activity == "offline" else 0.42) * k)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(c.x() - 2.6, c.y() - 7, 5.2, 8.6), 2.6, 2.6)
        p.setPen(QPen(col, 1.3, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        cradle = QPainterPath()
        cradle.moveTo(c.x() - 4.6, c.y() - 1.6)
        cradle.cubicTo(c.x() - 4.6, c.y() + 4.6, c.x() + 4.6, c.y() + 4.6, c.x() + 4.6, c.y() - 1.6)
        p.drawPath(cradle)
        p.drawLine(QPointF(c.x(), c.y() + 3.2), QPointF(c.x(), c.y() + 6.2))
        if self.tasks:
            p.setPen(Qt.NoPen)
            p.setBrush(d.alpha(d.color("background"), 0.55 * k))
            p.drawEllipse(QPointF(cap.right() - 16, c.y()), 2.5, 2.5)

    def draw_compact(self, p, cap, text):
        self.draw_orb(p, QPointF(cap.left() + 22, cap.center().y()), 9)
        p.setFont(self.font_pill)
        p.setPen(d.TERTIARY if self.activity == "offline" else d.SECONDARY)
        r = QRectF(cap.left() + 40, cap.top(), cap.width() - 54, cap.height())
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, int(r.width())))

    def draw_expanded(self, p, cap, label, text, dim, footer, lines):
        a, t = self.activity, time.time() - self.t0
        icon = QPointF(cap.left() + 31, cap.top() + 32)
        if self.bare(self.content):
            left = cap.left() + 22
        elif a == "listening":
            self.draw_mic(p, icon)
            left = cap.left() + 62
        else:
            self.draw_orb(p, icon, 13)
            left = cap.left() + 62
        top, right = cap.top() + 14, cap.right() - 22
        p.setFont(self.font_label)
        label_rect = QRectF(left, top, right - left, 16)
        if a == "thinking":                                   # a soft highlight sweeping across the label
            x = label_rect.left() + ((t * 0.55) % 1.6 - 0.3) * 260
            g = QLinearGradient(x - 60, 0, x + 60, 0)
            base = d.alpha(d.LABEL, 0.55)
            g.setColorAt(0, base)
            g.setColorAt(0.5, d.LABEL)
            g.setColorAt(1, base)
            p.setPen(QPen(g, 1))
        elif a == "idle" and lines:
            p.setPen(d.color("background"))
        else:
            p.setPen(d.SECONDARY if a in ("speaking", "idle") else d.color(a))
        p.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(label, Qt.ElideRight, int(label_rect.width())))
        body_top = top + 20
        bottom = cap.bottom() - 14 - (20 if footer else 0)
        if lines:
            p.setFont(self.font_small)
            fm = p.fontMetrics()
            for i, (dur, desc) in enumerate(lines):
                y = body_top + i * 20
                p.setPen(d.SECONDARY)
                p.drawText(QRectF(left, y, 56, 20), Qt.AlignLeft | Qt.AlignVCenter, dur)
                p.setPen(d.LABEL)
                p.drawText(QRectF(left + 60, y, right - left - 60, 20), Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText(desc, Qt.ElideRight, int(right - left - 60)))
        elif text:
            p.setFont(self.font_text)
            p.setPen(d.SECONDARY if dim else d.LABEL)
            box = QRectF(left, body_top, right - left, bottom - body_top)
            p.drawText(box, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.fit(p, text, box))
        if footer:
            p.setFont(self.font_label)
            p.setBrush(d.color("background"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(left + 3, cap.bottom() - 22), 3, 3)
            p.setPen(d.alpha(d.color("background"), 0.75))
            p.drawText(QRectF(left + 12, cap.bottom() - 30, right - left, 16), Qt.AlignLeft | Qt.AlignVCenter, footer)

    def draw_mic(self, p, c):
        """SF Symbols style mic.fill on a disc; only the mic glows, pulsing with your voice."""
        t, level, blue = time.time() - self.t0, self.mic, d.color("listening")
        p.setPen(Qt.NoPen)
        halo_r = 17 + 16 * level
        halo = QRadialGradient(c, halo_r)
        halo.setColorAt(0, d.alpha(blue, 0.30 + 0.45 * level))
        halo.setColorAt(1, d.alpha(blue, 0))
        p.setBrush(halo)
        p.drawEllipse(c, halo_r, halo_r)
        phase = (t % 1.4) / 1.4                                   # a ripple leaving the disc while you talk
        if level > 0.05:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(d.alpha(blue, 0.6 * level * (1 - phase)), 1.5))
            p.drawEllipse(c, 15 + 10 * phase, 15 + 10 * phase)
            p.setPen(Qt.NoPen)
        r = 15 * (1 + 0.06 * level)
        disc = QRadialGradient(c - QPointF(0, r * 0.5), r * 1.6)
        disc.setColorAt(0, blue.lighter(125))
        disc.setColorAt(1, blue.darker(115))
        p.setBrush(disc)
        p.drawEllipse(c, r, r)
        s = r / 15                                                # the glyph: capsule, cradle, stem, foot
        p.setBrush(d.on(blue))
        p.drawRoundedRect(QRectF(c.x() - 3.6 * s, c.y() - 8.2 * s, 7.2 * s, 11 * s), 3.6 * s, 3.6 * s)
        pen = QPen(d.on(blue), 1.7 * s, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cradle = QPainterPath()
        cradle.moveTo(c.x() - 6 * s, c.y() - 1.5 * s)
        cradle.cubicTo(c.x() - 6 * s, c.y() + 6.5 * s, c.x() + 6 * s, c.y() + 6.5 * s, c.x() + 6 * s, c.y() - 1.5 * s)
        p.drawPath(cradle)
        p.drawLine(QPointF(c.x(), c.y() + 4.6 * s), QPointF(c.x(), c.y() + 8 * s))
        p.drawLine(QPointF(c.x() - 3.2 * s, c.y() + 8 * s), QPointF(c.x() + 3.2 * s, c.y() + 8 * s))

    def draw_orb(self, p, c, r):
        """The orb: Jarvis's face. It swirls while thinking and breathes while waiting for an answer."""
        a, t = self.activity, time.time() - self.t0
        if a in ("thinking", "waiting", "ready"):
            r *= 1 + 0.05 * math.sin(t * 2.4)                 # breathing
        if a == "offline":
            p.setPen(QPen(d.alpha(d.GREY, 0.7), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r, r)
            return
        lit = a != "idle" or bool(self.tasks)
        base = d.color("background" if a == "idle" and self.tasks else a)
        p.setPen(Qt.NoPen)
        if lit:                                               # a soft, quiet glow
            halo = QRadialGradient(c, r * 1.9)
            halo.setColorAt(0, d.alpha(base, 0.32))
            halo.setColorAt(1, d.alpha(base, 0))
            p.setBrush(halo)
            p.drawEllipse(c, r * 1.9, r * 1.9)
        if a == "thinking":                                   # a soft disc
            g = QRadialGradient(c, r)
            g.setColorAt(0, d.alpha(base, 0.95))
            g.setColorAt(1, d.alpha(base, 0.55))
        else:
            g = QRadialGradient(c - QPointF(r * 0.2, r * 0.3), r * 1.25)
            g.setColorAt(0, base.lighter(150) if lit else QColor(150, 150, 158))
            g.setColorAt(0.55, base if lit else QColor(96, 96, 104))
            g.setColorAt(1, base.darker(160) if lit else QColor(58, 58, 64))
        p.setBrush(g)
        p.drawEllipse(c, r, r)
        if a == "thinking":                                   # with a thin highlight circling it
            p.setPen(QPen(d.alpha(base, 0.75), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(c.x() - r - 4, c.y() - r - 4, 2 * r + 8, 2 * r + 8), int(-t * 220 * 16) % 5760, 80 * 16)
            return
        gloss = QRadialGradient(c - QPointF(0, r * 0.45), r * 0.8)  # a glassy highlight on top
        gloss.setColorAt(0, QColor(255, 255, 255, 38))
        gloss.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(gloss)
        p.drawEllipse(c, r, r)
        if self.tasks and a == "idle":                        # background work: an amber dot circling the orb
            ang = t * 1.6
            p.setBrush(d.color("background"))
            p.drawEllipse(c + QPointF(math.cos(ang), math.sin(ang)) * (r + 6), 2.5, 2.5)

    @staticmethod
    def fit(p, text, box):
        """Show the END of long text (the newest words), trimmed from the front."""
        flags = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap
        if p.boundingRect(box, flags, text).height() <= box.height():
            return text
        words = text.split()
        while len(words) > 1:
            words.pop(0)
            if p.boundingRect(box, flags, "... " + " ".join(words)).height() <= box.height():
                break
        return "... " + " ".join(words)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("jarvis-widget")
    app.setDesktopFileName("jarvis-widget")
    w = Widget()
    screen, anchors, margins = screen_and_margins()
    overlay.layer_surface(w, screen, anchors, margins)      # pinned in place, never takes focus
    feed = Feed()
    feed.event.connect(w.on_event)
    w.show()
    feed.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
