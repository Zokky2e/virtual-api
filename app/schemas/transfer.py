"""Request shape for moving/copying an item between the personal and
shared trees (see app/services/transfer_service.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# The client names a tree, not an owner id. Owner ids are resolved
# server-side from the bearer token and SHARED_OWNER_ID — a route must
# never take an owner_id from the caller (see auth/dependencies.py).
Tree = Literal["personal", "shared"]


class TransferRequest(BaseModel):
    item_id: str
    source: Tree
    destination: Tree
    parent_folder_id: str | None = None
    # False copies, leaving the original in place; True moves it.
    move: bool
    # Name for the copy. Honoured for copies only — the client
    # resolves 'report (copy).pdf' against the destination folder the
    # same way a within-tree paste does, so the two behave alike. A
    # move keeps the item's own name and 409s on a clash.
    name: str | None = None
