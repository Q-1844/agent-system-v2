"""Per-async-task identity of a native video POST before its provider call.

The native handler already records its immutable dispatch signature and
submission intent in JobQueue/Shot. The C2 acceptance CAS mirrors these
identifiers without changing public Agnes API arguments or job protocols.
"""
from contextvars import ContextVar
from typing import Optional

active_video_submission: ContextVar[Optional[dict]] = ContextVar(
    'a_active_video_submission', default=None)
