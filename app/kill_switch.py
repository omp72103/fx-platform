"""
Operator kill switch — blueprint section 14 ("kill switch") and section 34.

The Risk Engine already REJECTs everything when AccountState.kill_switch_active
is True, but nothing used to ever set it. This module is that missing input.

Two ways to trip it, both deliberately simple so they work even if the API or
database is down:

  1. Environment:  KILL_SWITCH=1  (checked on every call, so a process
     restart with the variable set also halts trading).
  2. File:         create the file named by KILL_SWITCH_FILE (default
     "./KILL_SWITCH"). `touch KILL_SWITCH` on the VPS stops all new orders
     within one loop cycle; `rm KILL_SWITCH` resumes.

It only ever blocks NEW orders. It does not close existing positions —
flattening is a separate, deliberate human decision.
"""
from __future__ import annotations
import os


def kill_switch_file_path() -> str:
    return os.getenv("KILL_SWITCH_FILE", "KILL_SWITCH")


def is_kill_switch_active() -> bool:
    if os.getenv("KILL_SWITCH", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.path.exists(kill_switch_file_path())
