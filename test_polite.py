"""Run: .venv/bin/python test_polite.py  (checks "don't talk over your calls": call detection and the hold/release rules)"""
import asyncio
import os
import time
from types import SimpleNamespace as NS

import config
import jarvis
from ears import CallWatch

jarvis.events.emit = lambda *a, **k: None                     # keep the real event log clean
notes = []
jarvis.subprocess.Popen = lambda cmd: notes.append(cmd)
config.POLITE_QUIET_S, config.POLITE_MAX_WAIT_S = 0.3, 0.2

# Call detection, shaped like real pactl output: Jarvis's own mic stream carries its pid only on its client;
# Discord's game capture isn't on a mic; a monitor source isn't a mic either.
me = str(os.getpid())
PA = {"clients": [{"index": 9, "properties": {"application.process.id": me, "application.process.binary": "python3.12"}}],
      "sources": [{"index": 256, "monitor_source": "wave.analog-stereo"}, {"index": 257, "monitor_source": ""}],
      "source-outputs": [
          {"source": 257, "client": "9", "properties": {}},
          {"source": 257, "client": "7", "properties": {"application.process.id": "107", "application.process.binary": "Discord"}},
          {"source": 4294967295, "client": "8", "properties": {"application.process.id": "107", "application.process.binary": "Discord"}},
          {"source": 256, "client": "5", "properties": {"application.process.id": "50", "application.process.binary": "obs"}}],
      "sink-inputs": [
          {"index": 2145, "client": "7", "properties": {"application.process.id": "107", "application.process.binary": "Discord"}},
          {"index": 6110, "client": "9", "properties": {}},
          {"index": 3000, "client": "4", "properties": {"application.process.id": "107", "application.process.binary": "spotify"}}]}
assert CallWatch.scan(PA.__getitem__) == (["Discord"], [2145]), CallWatch.scan(PA.__getitem__)
assert CallWatch.scan(lambda what: {**PA, "source-outputs": PA["source-outputs"][:1]}[what]) == ([], [])

for said in ("Go ahead.", "Yeah, go ahead, sir.", "Okay.", "What is it?"):
    assert jarvis.GO_AHEAD.match(said), said
for said in ("Open Spotify.", "Go ahead and open Spotify."):
    assert not jarvis.GO_AHEAD.match(said), said


def fake(apps):
    async def update():
        pass
    return NS(mouth=NS(generation=0, playing=False), call=NS(apps=apps, last_heard=0.0, since=0.0, update=update),
              state=jarvis.IDLE, deliver=None, held=None, last_voice=0.0)


async def talking(j, attr, until, obj=None):
    while time.time() < until:
        setattr(obj or j, attr, time.time())
        await asyncio.sleep(0.05)


async def timed(j, token="update"):
    t = time.time()
    await jarvis.Jarvis.hold_for_quiet(j, token, "The video edit worker is done.")
    return time.time() - t


async def main():
    j = fake([])                                               # not on a call: straight through
    assert await timed(j) < 0.05 and j.held is None

    j = fake(["Discord"])                                      # the others talk for 0.4 s: held, then quiet
    talk = asyncio.create_task(talking(j, "last_heard", time.time() + 0.4, j.call))
    hold = asyncio.create_task(timed(j))
    await asyncio.sleep(0.2)
    assert j.held == "update"
    took = await hold
    await talk
    assert 0.65 < took < 1.0 and j.held is None, took
    assert len(notes) == 1 and notes[0][0] == "notify-send", notes    # held past the cap: a notification, still waited

    j = fake(["Discord"])                                      # the user never stops, but asks for it with "Hey Jarvis"
    talk = asyncio.create_task(talking(j, "last_voice", time.time() + 2))
    hold = asyncio.create_task(timed(j))
    await asyncio.sleep(0.2)
    j.deliver, j.state = "update", jarvis.LISTEN               # wake word: plays once he's said his piece
    await asyncio.sleep(0.2)
    assert not hold.done()
    j.state = jarvis.IDLE
    assert await hold < 0.6
    talk.cancel()

    j = fake(["Discord"])                                      # a held update cancelled by "stop"
    j.last_voice = time.time() + 5
    hold = asyncio.create_task(timed(j))
    await asyncio.sleep(0.2)
    j.mouth.generation += 1
    assert await hold < 0.4 and j.held is None
    print("ok")

asyncio.run(main())
