"""
Cross-tree transfer — the one endpoint that touches both the personal and
the shared tree, mounted at POST /desktop/transfer.

Its own router for the same reason shared.py is: files.py and folders.py
resolve ownership from the caller's uid and shared.py hardcodes
SHARED_OWNER_ID, so neither can express "from one to the other" without
growing exactly the "is this shared?" branch those routers exist to
avoid.

Notifications go to *both* trees — the item appears in one and disappears
from the other — and each side is broadcast the way that tree normally
is: to the owning user for a personal tree, to everyone for the shared
one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_notification_service, get_transfer_service
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.constants import SHARED_OWNER_ID
from app.schemas.file import FileResponse
from app.schemas.transfer import TransferRequest
from app.services.notification_service import NotificationService
from app.services.transfer_service import TransferService

router = APIRouter(tags=["transfer"])


@router.post("/transfer", response_model=FileResponse)
async def transfer_item(
    body: TransferRequest,
    user: AuthUser = Depends(get_current_user),
    transfers: TransferService = Depends(get_transfer_service),
    notifications: NotificationService = Depends(get_notification_service),
) -> FileResponse:
    def owner_for(tree: str) -> str:
        return SHARED_OWNER_ID if tree == "shared" else user.uid

    source_owner_id = owner_for(body.source)
    destination_owner_id = owner_for(body.destination)

    result = await transfers.transfer(
        item_id=body.item_id,
        source_owner_id=source_owner_id,
        destination_owner_id=destination_owner_id,
        destination_parent_folder_id=body.parent_folder_id,
        move=body.move,
        name=body.name,
    )

    await notifications.item_entered_tree(destination_owner_id, result.record)
    if result.removed_from_source:
        # The record's own parent_folder_id already points at the
        # destination, so the source tree is told the old one explicitly.
        await notifications.item_left_tree(
            source_owner_id, result.record, result.source_parent_folder_id
        )

    return FileResponse.model_validate(result.record)
