# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Resolve a process policy user or group to a numeric id."""

from __future__ import annotations

_POSIX_ID_MAX = 2**32 - 1


def resolve_posix_id(value: str, *, kind: str) -> int:
    """Return a uid or gid.

    A string of digits is the id itself, the same rule Conch uses.
    Any other string is looked up as a user or group name.

    ``pwd``/``grp`` are POSIX-only and imported lazily so this module stays
    importable on Windows (where only non-bubblewrap runtimes are used);
    calling this with a non-numeric name on Windows still fails, just at
    call time instead of import time.
    """
    text = value.strip()
    if text.isdigit():
        number = int(text)
        if number > _POSIX_ID_MAX:
            raise ValueError(f"{kind} id {number} is out of range")
        return number
    try:
        if kind == "user":
            import pwd
            return int(pwd.getpwnam(text).pw_uid)
        import grp
        return int(grp.getgrnam(text).gr_gid)
    except KeyError as exc:
        raise ValueError(f"{kind} {value!r} could not be resolved") from exc
