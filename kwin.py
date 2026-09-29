"""Talk to KWin (the KDE window manager): list/focus windows, read the cursor, screen layout.

KWin scripts can't return values, so each script calls back into a tiny DBus service
(org.jarvis.Bridge) that Jarvis owns, with a request id so answers never get mixed up.
"""
import asyncio
import itertools
import json
import os
import subprocess

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

import config

SCRIPT_PATH = os.path.join(config.RUNTIME_DIR, f"jarvis-kwin-{os.getpid()}.js")
BUS_NAME = f"org.jarvis.Bridge.p{os.getpid()}"     # one per process, so test scripts don't steal Jarvis's answers
_ids = itertools.count(1)


class _Bridge(ServiceInterface):
    def __init__(self):
        super().__init__("org.jarvis.Bridge")
        self.waiting = {}

    @method()
    def Report(self, req_id: 's', data: 's') -> 's':
        fut = self.waiting.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(data)
        return "ok"


_bridge = _Bridge()
_bus = None
_lock = asyncio.Lock()


async def start():
    global _bus
    if _bus:
        return
    _bus = await MessageBus().connect()
    _bus.export("/", _bridge)
    await _bus.request_name(BUS_NAME)


def _qdbus(*args):
    return subprocess.run(["qdbus", "org.kde.KWin", *args], capture_output=True, text=True, timeout=5).stdout.strip()


async def run(js_value):
    """Evaluate a JS expression inside KWin and return it (JSON round trip)."""
    await start()
    async with _lock:
        req = str(next(_ids))
        fut = asyncio.get_running_loop().create_future()
        _bridge.waiting[req] = fut
        with open(SCRIPT_PATH, "w") as f:
            f.write(f'callDBus("{BUS_NAME}", "/", "org.jarvis.Bridge", "Report", "{req}", '
                    f'JSON.stringify((function() {{ {js_value} }})()));')
        name = f"jarvis-{os.getpid()}-{req}"
        sid = _qdbus("/Scripting", "org.kde.kwin.Scripting.loadScript", SCRIPT_PATH, name)
        _qdbus(f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
        try:
            return json.loads(await asyncio.wait_for(fut, 3))
        finally:
            _bridge.waiting.pop(req, None)
            _qdbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", name)


WINDOW_FIELDS = """function info(w) { return {id: w.internalId.toString(), title: w.caption, app: w.resourceClass,
    x: w.frameGeometry.x, y: w.frameGeometry.y, w: w.frameGeometry.width, h: w.frameGeometry.height,
    active: w === workspace.activeWindow, minimized: w.minimized}; }"""


async def windows():
    return await run(WINDOW_FIELDS + " return workspace.windowList().filter(w => w.normalWindow).map(info);")


async def active_window():
    return await run(WINDOW_FIELDS + " return workspace.activeWindow ? info(workspace.activeWindow) : null;")


async def focus(query):
    q = json.dumps(query.lower())
    return await run(WINDOW_FIELDS + f"""
        var q = {q};
        var ws = workspace.windowList().filter(w => w.normalWindow);
        var hit = ws.find(w => w.resourceClass.toLowerCase() === q)
               || ws.find(w => w.caption.toLowerCase().includes(q))
               || ws.find(w => w.resourceClass.toLowerCase().includes(q));
        if (!hit) return null;
        hit.minimized = false;
        workspace.activeWindow = hit;
        return info(hit);""")


async def cursor():
    return await run("return {x: workspace.cursorPos.x, y: workspace.cursorPos.y};")


async def screens():
    return await run("return workspace.screens.map(s => ({name: s.name, x: s.geometry.x, y: s.geometry.y, "
                     "w: s.geometry.width, h: s.geometry.height}));")
