"""Shared debug flag handling for SDK-managed CLI output."""

import sys
from typing import Optional

COZE_DEBUG_ARG = "--coze-debug"

_debug_enabled: Optional[bool] = None


def coze_debug_enabled() -> bool:
    """Return whether SDK debug output is enabled for this process."""
    global _debug_enabled
    if _debug_enabled is None:
        _debug_enabled = _consume_debug_arg()
    return _debug_enabled


def coze_debug_print(*args, **kwargs):
    """Print only when --coze-debug was provided."""
    if coze_debug_enabled():
        print(*args, **kwargs)


def _consume_debug_arg() -> bool:
    argv = sys.argv
    found = False
    kept = [argv[0]] if argv else []
    for arg in argv[1:]:
        if arg == COZE_DEBUG_ARG or arg.startswith(f"{COZE_DEBUG_ARG}="):
            found = True
            continue
        kept.append(arg)

    if found:
        sys.argv[:] = kept
    return found


def _reset_coze_debug_for_tests():
    """Reset cached debug state for tests."""
    global _debug_enabled
    _debug_enabled = None
