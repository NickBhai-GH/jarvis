"""Run: .venv/bin/python test_time_tag.py  (checks the greeting tags jarvis.py adds to each message)"""
import json, os, tempfile, time
import config
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
from jarvis import time_tag

assert "first today" in time_tag()                       # nothing heard yet
t = time_tag(); assert "first" not in t and "back" not in t    # same day, no gap: plain tag
st = json.load(open(config.STATE_FILE)); st["last_heard"] -= 5 * 3600; json.dump(st, open(config.STATE_FILE, "w"))
assert "back after about 5 hours" in time_tag()
print("ok", time_tag())
