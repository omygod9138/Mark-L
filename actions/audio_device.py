"""CoreAudio default-output-device watcher (macOS only).

PortAudio snapshots the device list at init and never notices a live
default-output change — verified experimentally. This module is how Mark-L
finds out the default changed at all; main.py's coordinator decides when to
actually reinitialize PortAudio (see `_run_audio_device_watcher`).
"""
import ctypes
import struct
import sys

import sounddevice as sd

# Module-level globals: the CFUNCTYPE proc object and the address struct
# used in the listener registration MUST stay alive for the process
# lifetime. If either is garbage collected, the HAL later calls into freed
# memory and the process crashes — this is the single most important detail
# in this file.
_proc         = None
_addr_default = None


def _fourcc(s: str) -> int:
    return struct.unpack(">I", s.encode())[0]


class _Addr(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope",    ctypes.c_uint32),
        ("mElement",  ctypes.c_uint32),
    ]


_SYSTEM_OBJECT       = ctypes.c_uint32(1)
_SEL_DEFAULT_OUTPUT  = _fourcc("dOut")
_SEL_RUNLOOP         = _fourcc("rnlp")
_SCOPE_GLOBAL        = _fourcc("glob")

_LISTENER_PROC_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32,
    ctypes.POINTER(_Addr), ctypes.c_void_p,
)

_lib = None


def _coreaudio():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        for fn in ("AudioObjectGetPropertyData", "AudioObjectSetPropertyData",
                   "AudioObjectAddPropertyListener"):
            getattr(lib, fn).restype = ctypes.c_int32
        _lib = lib
    return _lib


def current_default_id() -> int | None:
    """Read kAudioHardwarePropertyDefaultOutputDevice. None on any failure."""
    if sys.platform != "darwin":
        return None
    try:
        lib  = _coreaudio()
        addr = _Addr(_SEL_DEFAULT_OUTPUT, _SCOPE_GLOBAL, 0)
        val  = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(val))
        status = lib.AudioObjectGetPropertyData(
            _SYSTEM_OBJECT, ctypes.byref(addr), ctypes.c_uint32(0), None,
            ctypes.byref(size), ctypes.byref(val),
        )
        return val.value if status == 0 else None
    except Exception:
        return None


_SEL_TRANSPORT_TYPE  = _fourcc("tran")
_TRANSPORT_BLUETOOTH = _fourcc("blue")


def current_output() -> tuple[str, str]:
    """(device_name, kind) for the current default output. kind is
    "headphones" or "speaker". Never raises — callers push this straight
    into UI code, so any failure just falls back to a sane default.
    """
    if sys.platform != "darwin":
        return ("Unknown", "speaker")
    try:
        name = sd.query_devices(kind="output")["name"]
    except Exception:
        return ("Unknown", "speaker")

    kind = "speaker"
    try:
        dev_id = current_default_id()
        if dev_id is not None:
            lib  = _coreaudio()
            addr = _Addr(_SEL_TRANSPORT_TYPE, _SCOPE_GLOBAL, 0)
            val  = ctypes.c_uint32(0)
            size = ctypes.c_uint32(ctypes.sizeof(val))
            status = lib.AudioObjectGetPropertyData(
                ctypes.c_uint32(dev_id), ctypes.byref(addr), ctypes.c_uint32(0), None,
                ctypes.byref(size), ctypes.byref(val),
            )
            if status == 0 and val.value == _TRANSPORT_BLUETOOTH:
                kind = "headphones"
    except Exception:
        pass
    # ponytail: wired headphones in the 3.5mm jack report transport "bltn"
    # (built-in), same as the internal speaker — only a data-source query
    # tells them apart, and that's another round-trip not worth it until
    # someone actually complains about the icon being wrong.
    return (name, kind)


def start(callback) -> bool:
    """Register a HAL listener that fires `callback()` on default-output change.

    Runs on a HAL thread with no CFRunLoop involved. Never raises: returns
    False on non-darwin or any ctypes/CoreAudio failure so Mark-L still runs
    with a fixed-at-launch output device.
    """
    global _proc, _addr_default

    if sys.platform != "darwin":
        return False

    try:
        lib = _coreaudio()

        # Route the listener onto the HAL's own thread instead of requiring a
        # CFRunLoop — must be set BEFORE adding the listener.
        addr_rl  = _Addr(_SEL_RUNLOOP, _SCOPE_GLOBAL, 0)
        null_ptr = ctypes.c_void_p(0)
        lib.AudioObjectSetPropertyData(
            _SYSTEM_OBJECT, ctypes.byref(addr_rl), ctypes.c_uint32(0), None,
            ctypes.c_uint32(8), ctypes.byref(null_ptr),
        )

        def _on_change(obj, num_addresses, addresses, client_data):
            # Called from a HAL thread — must never let an exception escape
            # into C, so it's wrapped and always returns 0 (noErr).
            try:
                callback()
            except Exception as e:
                print(f"[Audio] Watcher callback error: {e}")
            return 0

        proc         = _LISTENER_PROC_TYPE(_on_change)
        addr_default = _Addr(_SEL_DEFAULT_OUTPUT, _SCOPE_GLOBAL, 0)

        status = lib.AudioObjectAddPropertyListener(
            _SYSTEM_OBJECT, ctypes.byref(addr_default), proc, None,
        )
        if status != 0:
            return False

        # Commit to module globals only on success — see warning above.
        _proc, _addr_default = proc, addr_default
        return True
    except Exception:
        return False


def demo() -> bool:
    ok     = start(lambda: print("[Audio] Default output device changed."))
    dev_id = current_default_id()
    print(f"[Audio] Watcher registered: {ok}")
    print(f"[Audio] Current default output device id: {dev_id}")
    if sys.platform == "darwin":
        assert ok, "Watcher failed to register on darwin"

    out = current_output()
    print(f"[Audio] current_output(): {out}")
    assert isinstance(out, tuple) and len(out) == 2, "current_output() must return a 2-tuple"
    assert out[1] in ("headphones", "speaker"), "kind must be 'headphones' or 'speaker'"

    return ok


if __name__ == "__main__":
    demo()
