# -*- coding: utf-8 -*-
"""Token budget policy for model calls — the single source of truth.

Why this module exists
----------------------
A model call whose ``max_tokens`` budget is too small does **not** fail loudly.
The model simply stops mid-answer.  When the answer is JSON, the parser rejects
the half-object and the caller degrades to a fallback path *without telling
anyone* — the exact failure round 03 found in the Producer collaboration
planner.

Round-03 real evidence (api.agnes-ai.cn, ``agnes-3.0-flash``, thinking=True,
the repository's own Producer planning prompt — see
``evidence/gateC_producer_token_budget_probe.json``)::

    max_tokens=1500   -> the visible answer stops inside the JSON object
                          ("...separate the contact and grip be") and
                          ``_parse_json`` refuses it -> deterministic_fallback
    max_tokens=3000   -> first budget that returned a complete, parseable plan
    max_tokens=8192   -> complete
    max_tokens=65536  -> complete

The repository used to hardcode a *different* small budget at every call site
(512 / 700 / 800 / 1024 / 1500 / 1600 / 2048 / 4096 / 6000), so the same silent
truncation could come back the next time anyone edits one of them.  The policy
now lives here and is enforced **at the client boundary**: a call site can no
longer ask for less than the safe floor, and can no longer ask for more than
the endpoint accepts.

Measured ceilings (real calls, 2026-10-28 — see
``evidence/round03_max_tokens_ceiling_probe.json`` and
``evidence/round03_max_tokens_other_routes_probe.json``)::

    Agnes text    agnes-3.0-flash          65536 accepted / 131072 -> HTTP 400
    SenseNova     sensenova-6.8-flash-lite  65536 accepted / 131072 -> HTTP 400
    Agnes vision  agnes-2.5-flash          65536 accepted / 131072 -> HTTP 400

So "as large as possible" has a real, measured answer: **65536**.  It is the
endpoint's own hard ceiling, and it is used as the default for every text call
that does not state a budget of its own.
"""
from __future__ import annotations

#: The largest budget every measured route accepts.  Asking for more is a hard
#: 400 ("max_tokens 不能超过 65536"), so this doubles as the default ceiling.
MAX_ALLOWED_TEXT_MAX_TOKENS = 65536

#: The default budget for a text call that states no budget of its own: the
#: largest value the endpoint accepts, i.e. "as large as possible".
DEFAULT_TEXT_MAX_TOKENS = MAX_ALLOWED_TEXT_MAX_TOKENS

#: The smallest budget that was ever measured to survive thinking mode without
#: truncating its own JSON answer.  Requests below this are raised, never
#: honoured: a too-small budget fails silently, which is worse than a bigger
#: ceiling.
MIN_SAFE_TEXT_MAX_TOKENS = 3000


def resolve_text_max_tokens(requested=None, *,
                            floor: int = MIN_SAFE_TEXT_MAX_TOKENS,
                            ceiling: int = MAX_ALLOWED_TEXT_MAX_TOKENS,
                            default: int = DEFAULT_TEXT_MAX_TOKENS) -> int:
    """Clamp a requested token budget into the safe window.

    * ``None`` (or anything unusable) -> ``default`` (as large as the endpoint
      accepts, so a caller that forgets to pass a budget still cannot
      truncate).
    * A value below ``floor`` -> ``floor`` (thinking mode truncates its own
      JSON below that; the failure is silent, so the budget is raised).
    * A value above ``ceiling`` -> ``ceiling`` (the endpoint rejects larger
      values with HTTP 400; clamping keeps the call working).

    Pure function, no network, no configuration — so it can be unit-tested and
    reused by every entry point.
    """
    if floor > ceiling:
        raise ValueError("token budget floor must not exceed the ceiling")
    value: int
    if requested is None:
        value = default
    else:
        try:
            value = int(requested)
        except (TypeError, ValueError):
            value = default
        if value <= 0:
            value = default
    if value < floor:
        value = floor
    if value > ceiling:
        value = ceiling
    return value
