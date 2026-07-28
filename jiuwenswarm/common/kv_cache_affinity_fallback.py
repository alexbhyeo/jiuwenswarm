# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fallback shims for openjiuwen.core.foundation.kv_cache.

The currently pinned openjiuwen commit (see uv.lock) predates this module -
importing it raises ModuleNotFoundError, which would otherwise crash every
module that imports KVCacheAffinityConfig/resolve_kvc_action_timeout at
top level. KV cache affinity is an opt-in, Ascend-provider-specific
optimization (every call site already "fails closed" to disabled when the
configured model provider isn't Ascend), so a no-op fallback here doesn't
change behavior for the overwhelming majority of configs - it just lets the
app start instead of hard-crashing on an unrelated openjiuwen version gap.
Re-export the real symbols transparently once openjiuwen provides them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from openjiuwen.core.foundation.kv_cache import (  # type: ignore[import-not-found]
        KVCacheAffinityConfig,
        resolve_kvc_action_timeout,
    )
except ImportError:

    @dataclass
    class KVCacheAffinityConfig:  # type: ignore[no-redef]
        enable_kv_cache_release: bool = False
        enable_kv_cache_affinity: bool = False

    def resolve_kvc_action_timeout(action: Any, scope: str, timeout: float) -> float:  # type: ignore[no-redef]
        return timeout


__all__ = ["KVCacheAffinityConfig", "resolve_kvc_action_timeout"]
