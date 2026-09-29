"""Jarvis desktop widget: a small always-on-top, click-through panel in a corner of a monitor.

The ring: still when idle, follows your voice while listening, bobs while thinking,
vibrates with his voice while speaking, turns yellow when he's waiting for yes/no.
The text: what you're saying (live), then what he's saying, sentence by sentence.

Run: systemctl --user start jarvis-widget   (starts and stops with Jarvis)
"""
import http.client
import json
import math
import os
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

import config

TITLE = "Jarvis Widget"
W, H = 440, 124
PORT = 8765

COLORS = {
    "idle": QColor(150, 156, 166),
    "listening": QColor(110, 168, 255),
    "thinking": QColor(232, 186, 96),
    "speaking": QColor(170, 225, 255),
    "waiting": QColor(255, 209, 102),
    "offline": QColor(95, 99, 106),
}
LABELS = {"idle": "Idle", "listening": "Listening", "thinking": "Thinking", "speaking": "Jarvis",
          "waiting": "Say yes or no", "offline": "Jarvis isn't running"}
TOOL_NAMES = {"screenshot": "looking at the screen", "click_at": "clicking", "move_mouse": "moving the mouse",
              "scroll": "scrolling", "type_text": "typing", "press_keys": "pressing keys", "open_app": "opening an app",
              "focus_window": "switching windows", "list_windows": "checking windows", "media": "media controls",
              "volume": "volume", "Bash": "running a command", "Read": "reading a file", "Write": "writing a file",
              "Edit": "editing a file", "Grep": "searching files", "Glob": "finding files", "WebSearch": "searching the web",
              "WebFetch": "reading a web page", "ToolSearch": "loading tools", "Agent": "sub-agent working",
              "navigate": "opening a page in Chrome", "computer": "working in Chrome", "read_page": "reading the page",
              "get_page_text": "reading the page", "find": "searching the page", "form_input": "filling a form"}


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
    if short in ("ToolSearch", "screenshot", "click_at", "move_mouse", "scroll"):
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


def place_window():
    """Wayland apps can't position themselves, so ask KWin to: corner, on top, on every desktop, not in the taskbar."""
    screen_pick = {"right": "screens[screens.length - 1]", "left": "screens[0]"}.get(
        config.WIDGET_SCREEN, f'(screens.find(function(x) {{ return x.name === "{config.WIDGET_SCREEN}"; }}) || screens[0])')
    x = f"s.x + s.width - {W} - {config.WIDGET_MARGIN_X}" if config.WIDGET_CORNER.endswith("right") \
        else f"s.x + {config.WIDGET_MARGIN_X}"
    y = f"s.y + {config.WIDGET_MARGIN_Y}" if config.WIDGET_CORNER.startswith("top") \
        else f"s.y + s.height - {H} - {config.WIDGET_MARGIN_Y}"
    js = f"""
        var screens = workspace.screens.slice().sort(function(a, b) {{ return a.geometry.x - b.geometry.x; }});
        var s = {screen_pick}.geometry;
        workspace.windowList().forEach(function(w) {{
            if (w.caption !== "{TITLE}") return;
            w.frameGeometry = {{x: {x}, y: {y}, width: {W}, height: {H}}};
            w.keepAbove = true; w.skipTaskbar = true; w.skipPager = true; w.skipSwitcher = true;
            w.onAllDesktops = true;
        }});"""
    path = os.path.join(config.RUNTIME_DIR, "jarvis-widget-place.js")
    with open(path, "w") as f:
        f.write(js)
    q = lambda *a: subprocess.run(["qdbus", "org.kde.KWin", *a], capture_output=True, text=True).stdout.strip()
    name = f"jarvis-widget-{os.getpid()}"
    q("/Scripting", "org.kde.kwin.Scripting.unloadScript", name)
    sid = q("/Scripting", "org.kde.kwin.Scripting.loadScript", path, name)
    q(f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
    q("/Scripting", "org.kde.kwin.Scripting.unloadScript", name)


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
        self.prev_said = ""
        self.step = ""
        self.question = ""
        self.tasks = {}                   # background work: task_id -> {description, started, last_tool}
        self.self_started = False
        self.listen_id = 0
        self.last_change = time.time()
        self.t0 = time.time()
        self.last_key = None
        self.font_label = QFont("Public Sans", 9)
        self.font_label.setWeight(QFont.DemiBold)
        self.font_text = QFont("Public Sans", 11)
        timer = QTimer(self)
        timer.timeout.connect(self.tick)
        timer.start(33)

    # ---------- events ----------

    def on_event(self, ev):
        if ev.get("kind") != "live":
            return
        if ev["activity"] != self.activity:
            self.activity = ev["activity"]
            self.last_change = time.time()
        self.mic_raw, self.out_raw = ev.get("mic", 0), ev.get("out", 0)
        self.question = ev.get("question", "")
        self.you, self.said, self.step = ev.get("you", ""), ev.get("said", ""), ev.get("step", "")
        self.self_started = ev.get("self_started", False)
        self.tasks = {t.get("task_id", str(i)): t for i, t in enumerate(ev.get("tasks", []))}

    def tick(self):
        # attack fast, release slow, so the ring feels alive rather than jittery
        for name in ("mic", "out"):
            raw, cur = getattr(self, name + "_raw"), getattr(self, name)
            setattr(self, name, cur + (raw - cur) * (0.5 if raw > cur else 0.12))
        # idle and offline don't move, so only redraw when something changed (saves CPU)
        moving = self.activity not in ("idle", "offline") or bool(self.tasks)
        key = (self.activity, self.you, self.said, self.step, self.question, len(self.tasks), int(time.time() // 30))
        if moving or key != self.last_key:
            self.last_key = key
            self.update()

    # ---------- drawing ----------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 6, 6)
        p.fillPath(bg, QColor(17, 19, 23, 230))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawPath(bg)
        self.draw_ring(p)
        self.draw_text(p)

    def draw_ring(self, p):
        t = time.time() - self.t0
        a = self.activity
        color = COLORS.get(a, COLORS["idle"])
        cx, cy, r = 62, H / 2, 30
        if a == "thinking":
            cy += math.sin(t * 2.6) * 7                           # bob
        if a == "idle" and self.tasks:
            color = COLORS["thinking"]
        path = QPainterPath()
        n = 120
        for i in range(n + 1):
            th = i / n * math.tau
            if a == "speaking":
                amp = 1.5 + self.out * 9                           # ripple with his voice
                rr = r + amp * (0.65 * math.sin(4 * th + t * 7) + 0.35 * math.sin(7 * th - t * 10))
            elif a == "listening":
                rr = r + 2 + self.mic * 14 * (0.7 + 0.3 * math.sin(5 * th + t * 6))
            elif a == "waiting":
                rr = r + 2 * math.sin(t * 3)
            else:
                rr = r
            x, y = cx + rr * math.cos(th), cy + rr * math.sin(th)
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        pen = QPen(color, 2.2)
        if a == "offline":
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        dot = QColor(color)
        dot.setAlpha(200 if a != "idle" else 110)
        p.setBrush(dot)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
        if self.tasks and a in ("idle", "thinking", "speaking", "listening"):
            ang = t * 1.4                                          # background work: a dot orbiting the ring
            p.setBrush(COLORS["thinking"])
            p.drawEllipse(QRectF(cx + (r + 9) * math.cos(ang) - 3.5, cy + (r + 9) * math.sin(ang) - 3.5, 7, 7))

    def draw_text(self, p):
        a = self.activity
        left, right = 112, W - 14
        label, text, dim = LABELS.get(a, a), "", False
        footer = ""
        if self.tasks and a != "idle":
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
        elif a == "idle" and self.tasks:
            n = len(self.tasks)
            label = f"Working in the background ({n})"
            now = time.time()
            text = "\n".join(f"{short_dur(now - t['started'])}   {t['description'] or 'Background job'}"
                              for t in sorted(self.tasks.values(), key=lambda t: t["started"])[:3])
        elif a == "idle":
            idle_for = time.time() - self.last_change
            if self.said and idle_for < 120:
                label, text, dim = "Jarvis", self.said, True
            else:
                text, dim = 'Say "Hey Jarvis"', True
        elif a == "offline":
            text, dim = "Waiting for it to start", True
        p.setFont(self.font_label)
        p.setPen(COLORS["thinking"] if (a == "idle" and self.tasks) else COLORS.get(a, COLORS["idle"]))
        p.drawText(QRectF(left, 12, right - left, 18), Qt.AlignLeft | Qt.AlignVCenter, label)
        p.setFont(self.font_text)
        p.setPen(QColor(232, 233, 235, 150 if dim else 245))
        bottom = H - 12 - (18 if footer else 0)
        box = QRectF(left, 32, right - left, bottom - 32)
        if a == "idle" and self.tasks:
            fm = p.fontMetrics()
            lines = [fm.elidedText(line, Qt.ElideRight, int(right - left)) for line in text.split("\n")]
            p.drawText(box, Qt.AlignLeft | Qt.AlignTop, "\n".join(lines))
        else:
            p.drawText(box, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.fit(p, text, box))
        if footer:
            p.setFont(self.font_label)
            p.setPen(COLORS["thinking"])
            p.drawText(QRectF(left, H - 28, right - left, 16), Qt.AlignLeft | Qt.AlignVCenter, footer)

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
    feed = Feed()
    feed.event.connect(w.on_event)
    w.show()
    QTimer.singleShot(400, place_window)
    QTimer.singleShot(2500, place_window)       # again, in case KWin moved it while mapping
    feed.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
