# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility helper for constructing openjiuwen Rail classes (and their
jiuwenswarm subclasses) whose accepted keyword arguments vary between
openjiuwen versions.

The currently pinned openjiuwen commit (see uv.lock) predates several
keyword arguments jiuwenswarm now passes to SkillEvolutionRail and its
subclasses - passing them raises a hard TypeError instead of the graceful
degradation the surrounding try/except blocks intend. Where the installed
signature has a genuine replacement (signal_trigger was replaced by the
richer evolution_trigger: EvolutionTriggerPoint enum on SkillEvolutionRail
and its non-team subclasses - NONE vs the AFTER_INVOKE default), translate
to it instead of just dropping the intent. Anything with no real
equivalent on this build (e.g. TeamSkillEvolutionRail has no
evolution_trigger param at all yet) is filtered out so construction still
succeeds, just without that specific behavior.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _translate_signal_trigger(accepted: set[str], kwargs: dict[str, Any]) -> dict[str, Any]:
    """signal_trigger (bool) -> evolution_trigger (EvolutionTriggerPoint),
    only when the target actually has evolution_trigger and not
    signal_trigger itself (i.e. only on builds where this rename happened).
    """
    if "signal_trigger" not in kwargs or "signal_trigger" in accepted:
        return kwargs
    if "evolution_trigger" not in accepted or "evolution_trigger" in kwargs:
        return kwargs
    try:
        from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
            EvolutionTriggerPoint,
        )
    except ImportError:
        return kwargs
    kwargs = dict(kwargs)
    signal_trigger = kwargs.pop("signal_trigger")
    kwargs["evolution_trigger"] = (
        EvolutionTriggerPoint.AFTER_INVOKE if signal_trigger else EvolutionTriggerPoint.NONE
    )
    return kwargs


def filter_rail_kwargs(cls: type, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate/filter ``kwargs`` down to what ``cls.__init__`` actually
    accepts on this installed openjiuwen version, without constructing
    ``cls``. Used both by :func:`construct_compat` (direct construction) and
    by call sites that hand the rail's kwargs to an openjiuwen-library
    function which constructs the rail internally (e.g.
    ``configure_skill_evolution_runtime``), where we can't intercept the
    constructor call itself.
    """
    try:
        signature = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return kwargs

    accepted = set(signature.parameters)
    has_var_keyword = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
    )
    if has_var_keyword:
        return kwargs

    kwargs = _translate_signal_trigger(accepted, kwargs)

    filtered = {k: v for k, v in kwargs.items() if k in accepted}
    dropped = kwargs.keys() - filtered.keys()
    if dropped:
        logger.debug(
            "[rail_compat] %s.__init__ doesn't accept %s on this openjiuwen "
            "version - dropping them",
            cls.__name__,
            sorted(dropped),
        )
    return filtered


def construct_compat(cls: type[T], /, **kwargs: Any) -> T:
    """Construct ``cls(**kwargs)``, translating or dropping any keyword
    ``cls.__init__`` doesn't actually accept on this installed version, so
    callers can pass the full, current keyword set without needing
    per-version conditionals.
    """
    return cls(**filter_rail_kwargs(cls, kwargs))


def set_skill_evolution_triggers_compat(
    rail: Any, *, signal_trigger: bool, review_trigger: bool
) -> None:
    """Hot-reload update for an already-constructed evolution rail's
    trigger config, mirroring construction-time translation.

    On builds where the rail was constructed with the newer
    ``evolution_trigger: EvolutionTriggerPoint`` param (detected via the
    ``_evolution_trigger`` instance attribute it sets), update that instead
    of the old ``signal_trigger`` bool - the base class's hook methods only
    ever consult ``self._evolution_trigger``, so setting the old attribute
    name here would be silently ignored. ``review_trigger`` has no live
    equivalent on those builds and is dropped. On older builds (no
    ``_evolution_trigger`` attribute), fall back to the legacy attributes.
    """
    if hasattr(rail, "_evolution_trigger"):
        try:
            from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
                EvolutionTriggerPoint,
            )
        except ImportError:
            return
        rail._evolution_trigger = (
            EvolutionTriggerPoint.AFTER_INVOKE if signal_trigger else EvolutionTriggerPoint.NONE
        )
        logger.debug(
            "[rail_compat] %s hot-reload: signal_trigger=%s -> _evolution_trigger=%s "
            "(review_trigger=%s has no equivalent on this build, dropped)",
            type(rail).__name__,
            signal_trigger,
            rail._evolution_trigger,
            review_trigger,
        )
        return
    rail.signal_trigger = signal_trigger
    rail.review_trigger = review_trigger
