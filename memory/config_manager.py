import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def get_persona() -> str:
    """Return the configured persona key, or 'jarvis' if not set."""
    return load_api_keys().get("persona", "jarvis") or "jarvis"


# Single source of truth for persona data. Everything persona-shaped reads
# from here — main.py's session config, the UI's display name, and the
# vision subsystem's voice. Adding a persona means adding one row.
PERSONAS = {
    "jarvis": {
        "prompt":  BASE_DIR / "core" / "personas" / "jarvis.txt",
        "voice":   "Charon",
        "name":    "JARVIS",          # plain name, used as the session identity
        "display": "J.A.R.V.I.S.",    # stylised, used in the UI
        "address": "sir",
    },
    "friday": {
        "prompt":  BASE_DIR / "core" / "personas" / "friday.txt",
        "voice":   "Aoede",
        "name":    "FRIDAY",
        "display": "F.R.I.D.A.Y.",
        "address": "Boss",
    },
}
DEFAULT_PERSONA = "jarvis"


def get_persona_data() -> dict:
    """The active persona's row, falling back to the default on an unknown key."""
    return PERSONAS.get(get_persona(), PERSONAS[DEFAULT_PERSONA])


def get_display_name() -> str:
    """Return the assistant name to show in the UI for the active persona.

    Non-jarvis personas always show their stylised persona name. For the
    jarvis persona, a user-configured custom assistant_name takes
    precedence (existing behavior), falling back to "JARVIS".
    """
    persona = get_persona()
    if persona != DEFAULT_PERSONA:
        return PERSONAS.get(persona, PERSONAS[DEFAULT_PERSONA])["display"]
    return get_assistant_name()


def get_address() -> str:
    """How the assistant addresses the user: the configured user_name if set,
    otherwise the active persona's default form of address."""
    return (load_api_keys().get("user_name") or "").strip() or get_persona_data()["address"]


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_widget_mode_enabled() -> bool:
    return load_api_keys().get("widget_mode_enabled", False)


def save_widget_mode_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["widget_mode_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_widget_pos() -> tuple[int, int] | None:
    pos = load_api_keys().get("widget_pos")
    if isinstance(pos, list) and len(pos) == 2:
        try:
            return (int(pos[0]), int(pos[1]))
        except Exception:
            return None
    return None


def save_widget_pos(x: int, y: int) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["widget_pos"] = [x, y]
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_voice_override() -> str:
    """Return the configured voice override, or '' if not set."""
    return load_api_keys().get("voice", "") or ""


def get_voice() -> str:
    """Resolved TTS voice: the `voice` config override if set, else the persona's."""
    return get_voice_override() or get_persona_data()["voice"]