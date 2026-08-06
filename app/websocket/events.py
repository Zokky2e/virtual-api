"""
WebSocket event definitions — mirrors the event shapes described in
Virtual_Desktop_Server_Architecture.md's WebSocket section, adapted to
use item ids (this API's identity) rather than filesystem paths.
NotificationService builds every outgoing message through `build_event`
so the payload shape only has to change in one place.
"""

from __future__ import annotations

from typing import Literal

EventName = Literal[
    "file_created",
    "file_renamed",
    "file_moved",
    "file_deleted",
    "file_restored",
    "folder_created",
    "folder_renamed",
    "folder_moved",
    "folder_deleted",
    "folder_restored",
]


def build_event(
    event: EventName,
    *,
    item_id: str,
    parent_folder_id: str | None,
    **extra: object,
) -> dict:
    return {
        "event": event,
        "id": item_id,
        "parent_folder_id": parent_folder_id,
        **extra,
    }