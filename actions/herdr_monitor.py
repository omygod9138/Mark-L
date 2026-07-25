"""Watches herdr for agents transitioning into "blocked" and hands speakable
workspace names to the main loop, which voices them through the Gemini
session. Polls `herdr agent list` every couple of seconds; fail-open — any
herdr error yields no events, never an exception.
"""
import json
import os
import subprocess
from pathlib import Path

SESSION_JSON = Path.home() / ".config/herdr/session.json"


class HerdrMissing(Exception):
    """herdr CLI not installed — caller should back off."""


def _list_agents() -> list:
    """herdr's live agent list, or [] on any error (watcher must not die)."""
    try:
        out = subprocess.run(
            ["herdr", "agent", "list"], capture_output=True, text=True, timeout=5
        ).stdout
        return json.loads(out).get("result", {}).get("agents", [])
    except FileNotFoundError:
        raise HerdrMissing()
    except Exception:
        return []


def _workspace_name(agent: dict) -> str:
    """Friendly, speakable name: herdr custom_name if set, else the cwd basename."""
    wsid = agent.get("workspace_id")
    try:
        data = json.loads(SESSION_JSON.read_text())
        for ws in data.get("workspaces", []):
            if ws.get("id") == wsid and ws.get("custom_name"):
                return str(ws["custom_name"])
    except Exception:
        pass
    base = os.path.basename(agent.get("cwd", "") or wsid or "a workspace")
    return base.replace("-", " ").replace("_", " ")


class HerdrWatcher:
    """Transition detector. check() returns names that JUST went blocked.
    The first call only primes state (no announcements for pre-existing blocks)."""

    def __init__(self):
        self._prev: dict = {}
        self._primed = False

    def check(self) -> list[str]:
        agents = _list_agents()
        fresh: list[str] = []
        for a in agents:
            pid, status = a.get("pane_id"), a.get("agent_status")
            if status == "blocked" and self._prev.get(pid) != "blocked" and self._primed:
                fresh.append(_workspace_name(a))
            self._prev[pid] = status
        self._primed = True
        return fresh
