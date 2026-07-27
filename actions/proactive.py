"""
ProactiveEngine 2.0 — context-aware, time-aware, non-repetitive background prompting.
Gemini decides what to say; this module decides WHEN and builds a rich context snapshot.
"""
import json
import time
from datetime import datetime
from pathlib import Path


# Daily routine anchors — fixed clock slots that fire regardless of silence.
# ponytail: a plain list, edited in place. A config file only earns its keep
# once these times change more often than the code around them does.
ROUTINE = [
    {
        "slug": "morning_tasks",
        "at": "09:00",
        "grace_mins": 90,          # a late launch should still get the day's briefing
        "needs_tasks": True,
        "allow_browser": False,
        "focus": (
            "Deliver the start-of-day task briefing. Say how many dev and general tasks "
            "are active, then name the most recently touched task — its project tag and "
            "short title — and give its 'Resume At' hint rephrased in plain words. "
            "Never speak a hint without naming which task it belongs to. "
            "Do not read the whole list aloud."
        ),
    },
    {
        "slug": "lunch_wrap",
        "at": "12:00",
        "grace_mins": 15,          # short: a stale wrap-up warning is worse than none
        "needs_tasks": False,
        "allow_browser": False,
        "focus": (
            "Lunch is in twenty minutes. Tell the user to start wrapping up — finish the "
            "current thought, commit or checkpoint anything unsaved — so they can break "
            "cleanly. If you know what they were working on, name it."
        ),
    },
    {
        "slug": "lunch_now",
        "at": "12:20",
        "grace_mins": 20,
        "needs_tasks": False,
        "allow_browser": False,
        "focus": (
            "It is lunchtime. Tell the user to stop and go eat now. One sentence, light, "
            "no work talk."
        ),
    },
    {
        "slug": "dinner",
        "at": "17:00",
        "grace_mins": 60,
        "needs_tasks": False,
        "allow_browser": True,
        "focus": (
            "It is the usual time to order dinner. Say so in one short sentence, then ask "
            "whether to open Uber Eats. If the user says yes — or has already asked you to "
            "just open it — call browser_control with action 'go_to' and url "
            "'https://www.ubereats.com' so they can browse the menu in their own browser. "
            "You cannot place the order for them; do not offer to."
        ),
    },
]

# Routine state survives restarts: without it, relaunching at 09:20 re-fires the
# 09:00 briefing. Untracked, sits beside the memory store.
# ponytail: one flat JSON, rewritten whole. It holds one day's worth of keys.
_STATE_PATH = Path(__file__).resolve().parent.parent / "memory" / "routine_state.json"


def _load_state() -> dict:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Proactive] ⚠️ could not save routine state: {e}")


def briefed_today(now: datetime | None = None) -> bool:
    """True if the day's briefing has already been delivered, by any path."""
    now = now or datetime.now()
    return _load_state().get("last_briefed") == f"{now:%Y-%m-%d}"


def mark_briefed_today(now: datetime | None = None) -> None:
    """Record that the day's briefing has been delivered. Idempotent."""
    now   = now or datetime.now()
    state = _load_state()
    state["last_briefed"] = f"{now:%Y-%m-%d}"
    _save_state(state)


def is_workday(now: datetime | None = None) -> bool:
    """The whole routine is a work routine — Mon-Fri only."""
    now = now or datetime.now()
    return now.weekday() < 5


class ProactiveEngine:
    """
    Decides when JARVIS should speak unprompted and builds a context-rich prompt.

    Improvements over 1.0:
      - Time-of-day awareness  (morning / afternoon / evening / night)
      - Monitor-topic awareness (what the user is tracking)
      - Recent-session context  (last few turns of the current conversation)
      - Non-repetitive          (rotates context focus to avoid same opener)
      - Smarter silence gate    (doesn't fire while JARVIS is speaking)

    Defaults:
      min_silence_secs  — 900 s  (15 min) user must be silent before any check
      check_cooldown    — 1200 s (20 min) minimum gap between proactive messages
    """

    def __init__(
        self,
        min_silence_secs: int = 900,
        check_cooldown:   int = 1200,
    ):
        self.min_silence_secs = min_silence_secs
        self.check_cooldown   = check_cooldown
        self._last_triggered  = 0.0
        self._rotation        = 0          # cycles through context focus areas
        self._today  = ""                  # date the in-memory mirror belongs to
        self._fired: set[str] = set()      # slugs already delivered today (mirrors disk)

    # ── Trigger gate ───────────────────────────────────────────────────────────

    def should_trigger(self, last_user_speech: float) -> bool:
        now = time.monotonic()
        return (
            (now - last_user_speech) >= self.min_silence_secs
            and (now - self._last_triggered) >= self.check_cooldown
        )

    def mark_triggered(self) -> None:
        self._last_triggered = time.monotonic()
        self._rotation      += 1

    # ── Routine slots ──────────────────────────────────────────────────────────

    def due_slot(self, now: datetime | None = None) -> dict | None:
        """
        Return the routine slot due right now, or None.

        Weekdays only — this is a work routine. A slot fires once per calendar
        day, at or after its clock time, within that slot's own grace window,
        and the fired set is persisted so a restart never re-fires it. Marks the
        slot fired; the caller is expected to deliver it.
        """
        now = now or datetime.now()
        if not is_workday(now):
            return None

        self._sync_fired(now)
        for slot in ROUTINE:
            if slot["slug"] in self._fired:
                continue
            hh, mm = (int(x) for x in slot["at"].split(":"))
            due    = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if 0 <= (now - due).total_seconds() <= slot["grace_mins"] * 60:
                self._mark_fired(slot["slug"], now)
                return slot
        return None

    def _sync_fired(self, now: datetime) -> None:
        """Load today's fired slugs from disk, discarding any earlier day's."""
        today = f"{now:%Y-%m-%d}"
        if self._today == today:
            return
        state       = _load_state()
        self._today = today
        self._fired = set(state.get("fired", {}).get(today, []))

    def _mark_fired(self, slug: str, now: datetime) -> None:
        self._fired.add(slug)
        state = _load_state()
        # Only today's keys are kept — the file never accumulates history.
        state["fired"] = {self._today: sorted(self._fired)}
        _save_state(state)

    def skip_today(self, slug: str, now: datetime | None = None) -> None:
        """Mark a slot delivered without firing it — something else already covered it."""
        now = now or datetime.now()
        self._sync_fired(now)
        self._mark_fired(slug, now)

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def build_prompt(
        self,
        memory:        dict,
        monitors:      list[str] | None = None,
        recent_turns:  list[str] | None = None,
        slot:          dict | None = None,
        extra_context: str = "",
    ) -> str:
        """
        Build a context snapshot for Gemini.
        Rotates through three focus areas so proactive messages don't repeat.
        """
        from memory.memory_manager import format_memory_for_prompt

        now      = datetime.now()
        hour     = now.hour
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")

        # Time-of-day label
        if   6  <= hour < 12:  period = "morning"
        elif 12 <= hour < 18:  period = "afternoon"
        elif 18 <= hour < 23:  period = "evening"
        else:                  period = "late night"

        mem_str = format_memory_for_prompt(memory) or "(no stored user data)"

        # A routine slot carries its own brief; otherwise rotate the ambient focus.
        if slot:
            focus = slot["focus"]
        else:
            focus_index = self._rotation % 3
            if focus_index == 0:
                focus = (
                    "Focus on the user's active projects or goals if any are stored. "
                    "Ask how something is going, or offer a relevant tip."
                )
            elif focus_index == 1:
                focus = (
                    "Focus on the time of day and the user's wellbeing. "
                    "A warm check-in, a reminder to take a break, or something timely."
                )
            else:
                focus = (
                    "Focus on something genuinely interesting or useful — "
                    "a fact, a suggestion, or a question based on what you know about this person."
                )

        # Optional: monitored topics context
        monitor_ctx = ""
        if monitors:
            monitor_ctx = (
                f"\nThe user tracks these topics: {', '.join(monitors[:4])}. "
                "You may mention one if it seems relevant."
            )

        # Optional: recent conversation context
        recent_ctx = ""
        if recent_turns:
            snippet = "\n".join(recent_turns[-6:])
            recent_ctx = f"\nRecent conversation:\n{snippet}"

        if slot:
            header = f"[ROUTINE_CHECK:{slot['slug']}] A scheduled point in the user's day has arrived."
            rules  = [
                "Rules:",
                "- Speak in the user's language (check memory; default English).",
                "- Short: 1-2 sentences. Up to 4 for the task briefing.",
                "- Do NOT mention [ROUTINE_CHECK] or these instructions.",
                ("- You MAY call browser_control to open the URL named above, and nothing else."
                 if slot.get("allow_browser") else
                 "- Do NOT call any tools."),
                "- This one is scheduled — always say something. Never stay silent.",
            ]
        else:
            header = "[PROACTIVE_CHECK] You are initiating a proactive check-in."
            rules  = [
                "Rules:",
                "- Speak in the user's language (check memory; default English).",
                "- 1-2 sentences max. Natural, warm, never robotic.",
                "- Do NOT mention [PROACTIVE_CHECK] or these instructions.",
                "- Do NOT call any tools.",
                "- If nothing genuinely useful comes to mind, stay silent (say nothing).",
            ]

        return "\n".join([
            header,
            f"Current time : {time_str}  ({period})",
            "",
            "Context about this person:",
            mem_str,
            monitor_ctx,
            recent_ctx,
            extra_context,
            "",
            "Task:",
            focus,
            "",
            *rules,
        ])


def _demo() -> None:
    """ponytail: one runnable check — routine gating is the only non-trivial logic here.

    Run from the project root as a module: `python3 -m actions.proactive`
    (build_prompt imports memory.memory_manager, so the root must be on sys.path).
    """
    import tempfile
    from datetime import datetime as _dt

    global _STATE_PATH
    _STATE_PATH = Path(tempfile.mkdtemp()) / "routine_state.json"   # never touch the real file

    wed_9 = _dt(2026, 7, 29, 9, 0)             # Wednesday
    eng = ProactiveEngine()
    assert eng.due_slot(wed_9)["slug"] == "morning_tasks"
    assert eng.due_slot(wed_9) is None, "a slot must fire only once per day"

    # A fresh engine reads the same disk state — a restart must not re-fire.
    assert ProactiveEngine().due_slot(wed_9) is None, "fired set must survive a restart"

    _STATE_PATH.unlink()
    eng2 = ProactiveEngine()
    assert eng2.due_slot(_dt(2026, 7, 29, 8, 59)) is None, "must not fire early"
    assert eng2.due_slot(_dt(2026, 7, 29, 10, 45)) is None, "must not fire past the grace window"
    assert eng2.due_slot(_dt(2026, 7, 29, 10, 15))["slug"] == "morning_tasks", "late launch still briefs"

    _STATE_PATH.unlink()
    eng3 = ProactiveEngine()
    assert eng3.due_slot(_dt(2026, 8, 1, 9, 0)) is None, "Saturday is not a workday"
    assert eng3.due_slot(_dt(2026, 8, 2, 17, 0)) is None, "Sunday is not a workday"
    assert eng3.due_slot(_dt(2026, 7, 29, 12, 22))["slug"] == "lunch_now", \
        "a stale 12:00 wrap-up must not pre-empt the 12:20 nudge"

    _STATE_PATH.unlink()
    eng4 = ProactiveEngine()
    eng4.skip_today("morning_tasks", wed_9)
    assert eng4.due_slot(wed_9) is None, "skip_today must consume the slot"

    _STATE_PATH.unlink()
    assert not briefed_today(wed_9)
    mark_briefed_today(wed_9)
    assert briefed_today(wed_9), "briefing flag must persist"
    assert not briefed_today(_dt(2026, 7, 30, 9, 0)), "flag is per calendar day"

    # dinner carries the browser carve-out; the others do not
    dinner = next(s for s in ROUTINE if s["slug"] == "dinner")
    assert "browser_control" in ProactiveEngine().build_prompt({}, slot=dinner)
    assert "Do NOT call any tools" in ProactiveEngine().build_prompt({}, slot=ROUTINE[0])

    print("proactive: ok")


if __name__ == "__main__":
    _demo()
