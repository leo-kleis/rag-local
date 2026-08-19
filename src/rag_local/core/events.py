from __future__ import annotations

import sys
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError

IPC_EVENT_PREFIX: str = "@@RAG_EVENT:"
IPC_EVENT_SUFFIX: str = "@@"


class SyncPhase(StrEnum):
    """Fases deterministas del ciclo de vida de sincronización e ingesta."""

    START = "sync_start"
    PROGRESS = "sync_progress"
    COMPLETED = "sync_completed"
    ERROR = "sync_error"


class SyncEvent(BaseModel):
    """Evento estructurado transmitido entre procesos vía stderr."""

    phase: SyncPhase
    progress: int = Field(default=0, ge=0, le=100)
    message: str = ""
    changed_count: int = 0
    reason: str = ""


def emit_sync_event(
    phase: SyncPhase,
    progress: int = 0,
    message: str = "",
    changed_count: int = 0,
    reason: str = "",
) -> None:
    """Serializa y emite un evento tipado por stderr de forma no bloqueante."""
    event = SyncEvent(
        phase=phase,
        progress=progress,
        message=message,
        changed_count=changed_count,
        reason=reason,
    )
    try:
        sys.stderr.write(
            f"{IPC_EVENT_PREFIX}{event.model_dump_json()}{IPC_EVENT_SUFFIX}\n"
        )
        sys.stderr.flush()
    except (BrokenPipeError, OSError):
        pass


def parse_sync_event(line: str) -> SyncEvent | None:
    """Extrae y valida un evento estructurado desde una línea de salida."""
    if IPC_EVENT_PREFIX not in line or IPC_EVENT_SUFFIX not in line:
        return None

    try:
        start_idx = line.find(IPC_EVENT_PREFIX) + len(IPC_EVENT_PREFIX)
        end_idx = line.find(IPC_EVENT_SUFFIX, start_idx)
        if start_idx != -1 and end_idx != -1:
            payload = line[start_idx:end_idx].strip()
            return SyncEvent.model_validate_json(payload)
    except (ValidationError, ValueError):
        return None
    return None
