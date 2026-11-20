# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Server-managed workspace paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform != "win32":
    import pwd


def _effective_user_home() -> Path:
    """Return the effective user's home directory without trusting $HOME.

    ``pwd``/``geteuid`` have no Windows equivalent (no effective-UID
    concept); ``Path.home()`` reads ``USERPROFILE`` there instead, which is
    not spoofable the same way a plain ``$HOME`` env var is on POSIX.
    """
    if sys.platform == "win32":
        return Path.home()
    try:
        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except KeyError:
        return Path.home()


JIUWENBOX_HOME = _effective_user_home() / ".jiuwenbox"
SANDBOX_WORKSPACE = JIUWENBOX_HOME / "workspace"
