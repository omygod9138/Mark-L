"""Relays Claude Code replies into the voice session, one pane at a time.

Every Claude Code session drops its final reply as a JSON file in QUEUE_DIR
(written by the Stop hook). Only the herdr pane that currently has focus gets
spoken; every other pane's reply is *held* and released when the user turns to
it, so a burst of parallel agents finishing at once can never talk over each
other. Unfocused panes are announced once as a reminder and then stay quiet.

Fail-open like HerdrWatcher: any herdr or filesystem error yields no events,
never an exception.
"""
import json
import subprocess
import time
from pathlib import Path

QUEUE_DIR = Path.home() / ".mark-l" / "claude_queue"
MAX_AGE   = 600    # a reply older than 10 min is stale — drop it, don't monologue
MAX_CHARS = 1500   # Gemini only summarises it; the tail earns nothing


def focused_pane() -> str | None:
    """herdr's currently focused pane id, or None if herdr is unreachable."""
    try:
        out = subprocess.run(
            ["herdr", "pane", "list"], capture_output=True, text=True, timeout=5
        ).stdout
        for p in json.loads(out).get("result", {}).get("panes", []):
            if p.get("focused"):
                return p.get("pane_id")
    except Exception:
        pass
    return None


class ClaudeRelay:
    """Focus-gated queue. check() returns (reply_to_speak, names_to_remind_about).

    Never both: a spoken reply and a reminder in the same tick would be the
    stacking this exists to prevent.
    """

    def __init__(self):
        self._held: dict[str, dict] = {}    # pane_id -> newest pending reply
        self._announced: set[str] = set()   # panes already reminded about

    @staticmethod
    def _drain_files() -> list[dict]:
        """Pop every queued file, oldest first (filenames are nanosecond stamps)."""
        try:
            paths = sorted(QUEUE_DIR.glob("*.json"))
        except Exception:
            return []
        items = []
        for p in paths:
            try:
                items.append(json.loads(p.read_text()))
            except Exception:
                pass
            try:
                p.unlink()
            except Exception:
                pass
        return items

    def check(self) -> tuple[dict | None, list[str]]:
        now = time.time()

        for item in self._drain_files():
            pid = item.get("pane_id")
            if pid:
                # Newest wins per pane: an agent that answered three times while
                # unfocused is one item to catch up on, not three.
                self._held[pid] = item

        for pid, item in list(self._held.items()):
            if now - item.get("ts", now) > MAX_AGE:
                del self._held[pid]

        cur = focused_pane()
        reply = self._held.pop(cur, None) if cur else None
        self._announced &= self._held.keys()

        if reply:
            reply["text"] = (reply.get("text") or "")[:MAX_CHARS]
            return reply, []

        fresh = [self._held[p].get("project") or "a project"
                 for p in self._held if p not in self._announced]
        self._announced |= self._held.keys()
        return None, fresh


def demo():
    """Self-check: focus gating, hold-until-focused, remind-once, newest-wins."""
    import actions.claude_relay as m

    r = ClaudeRelay()
    holder = {"items": []}
    r._drain_files = lambda: holder["items"]

    def feed(items):
        holder["items"] = items

    m.focused_pane = lambda: "w1:p1"
    feed([{"pane_id": "w1:p1", "project": "alpha", "text": "a", "ts": time.time()},
          {"pane_id": "w2:p1", "project": "beta",  "text": "b", "ts": time.time()}])
    reply, rem = r.check()
    assert reply["project"] == "alpha" and rem == [], (reply, rem)

    feed([])
    reply, rem = r.check()                      # beta is held, reminded once
    assert reply is None and rem == ["beta"], (reply, rem)
    reply, rem = r.check()                      # ...and not nagged again
    assert reply is None and rem == [], (reply, rem)

    m.focused_pane = lambda: "w2:p1"            # user turns to beta
    reply, rem = r.check()
    assert reply["project"] == "beta" and rem == [], (reply, rem)

    m.focused_pane = lambda: "w1:p1"            # newest reply per pane wins
    feed([{"pane_id": "w2:p1", "project": "beta", "text": "old", "ts": time.time()},
          {"pane_id": "w2:p1", "project": "beta", "text": "new", "ts": time.time()}])
    r.check()
    feed([])
    assert list(r._held) == ["w2:p1"] and r._held["w2:p1"]["text"] == "new", r._held

    feed([{"pane_id": "w3:p1", "project": "gamma", "text": "g", "ts": time.time() - 999}])
    r.check()
    assert "w3:p1" not in r._held, "stale reply should have been dropped"

    print("[ClaudeRelay] self-check ok")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    # `python -m` runs this file as "__main__" while demo() re-imports it under
    # its real package name — two separate module objects unless we force the
    # real import first, which is what lets demo()'s monkeypatch reach check().
    import actions.claude_relay as _self
    _self.demo()
