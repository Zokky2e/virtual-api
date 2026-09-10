"""
Standalone check that WebSocket events go out only once the change they
announce has been committed. Not a pytest suite (this project has none
yet) — run it directly:

    python -m scripts.check_event_order

The Flutter client re-fetches a folder the moment an event names it. That
re-fetch is a separate request on its own connection, so it can only see
committed rows: an event that beats the commit sends the client to read
the state from before the change, and nothing afterwards tells it to look
again. The window stays stale until it is closed and reopened.

The connected "client" below does exactly that re-fetch from inside
send_json, and records what it could see at the moment each event arrived.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

# Settings are read once and the engine is built at import time, so the
# throwaway paths have to be in the environment before `app` is imported.
_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["DATABASE_PATH"] = str(Path(_tmp.name) / "test.db")
os.environ["STORAGE_ROOT"] = str(Path(_tmp.name) / "storage")
os.environ.setdefault("FIREBASE_PROJECT_ID", "check-event-order")

import httpx  # noqa: E402
from fastapi import Depends  # noqa: E402

from app.api.dependencies import (  # noqa: E402
    get_folder_service,
    get_notification_service,
)
from app.auth.dependencies import get_current_user  # noqa: E402
from app.auth.models import AuthUser  # noqa: E402
from app.constants import SHARED_OWNER_ID  # noqa: E402
from app.database.database import (  # noqa: E402
    async_session_factory,
    engine,
    init_db,
)
from app.database.repositories import FileRepository  # noqa: E402
from app.exceptions import InvalidOperationError  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.folder_service import FolderService  # noqa: E402
from app.services.notification_service import NotificationService  # noqa: E402
from app.websocket.manager import manager  # noqa: E402

ALICE = "alice"

_failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        _failures.append(label)


class RefetchingClient:
    """A connected socket that behaves like the Flutter client: on every
    event it re-reads each folder the event names, in a session of its own."""

    def __init__(self) -> None:
        self._received: list[tuple[dict, dict[str | None, set[str]]]] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, message: dict) -> None:
        folder_ids = {message["parent_folder_id"]}
        if "old_parent_folder_id" in message:
            folder_ids.add(message["old_parent_folder_id"])
        async with async_session_factory() as session:
            repo = FileRepository(session)
            visible = {
                folder_id: {
                    child.id
                    for child in await repo.get_folder(message["owner_id"], folder_id)
                }
                for folder_id in folder_ids
            }
        self._received.append((message, visible))

    def take(self) -> list[tuple[dict, dict[str | None, set[str]]]]:
        received, self._received = self._received, []
        return received


def seen_in(events, event: str, owner_id: str, folder_id: str | None):
    """Ids the client could see in `folder_id` when `event` for that tree
    arrived — or None if no such event arrived at all."""
    for message, visible in events:
        if (
            message["event"] == event
            and message["owner_id"] == owner_id
            and folder_id in visible
        ):
            return visible[folder_id]
    return None


async def main() -> None:
    await init_db()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthUser(uid=ALICE)

    # A route no real client calls: it records an event, then fails, so the
    # transaction behind that event is rolled back.
    @app.post("/check/notify-then-fail")
    async def notify_then_fail(
        folders: FolderService = Depends(get_folder_service),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> None:
        record = await folders.create_folder(
            owner_id=ALICE, name="Doomed", parent_folder_id=None
        )
        await notifications.item_created(ALICE, record)
        raise InvalidOperationError("failing after the event was recorded")

    client = RefetchingClient()
    await manager.connect(ALICE, client)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://check") as http:
        print("create a folder in the personal root")
        res = await http.post("/desktop/folder", json={"name": "Watched"})
        watched = res.json()["id"]
        seen = seen_in(client.take(), "folder_created", ALICE, None)
        check("folder_created arrived", seen is not None)
        check("the folder is there when its event arrives", watched in (seen or set()))

        print("create a folder in the shared root")
        res = await http.post("/desktop/shared/folder", json={"name": "Captain Marvel"})
        film = res.json()["id"]
        seen = seen_in(client.take(), "folder_created", SHARED_OWNER_ID, None)
        check("folder_created arrived", seen is not None)
        check("the folder is there when its event arrives", film in (seen or set()))

        print("move a folder within the personal tree")
        res = await http.post("/desktop/folder", json={"name": "Archive"})
        archive = res.json()["id"]
        client.take()
        res = await http.patch(
            "/desktop/move", json={"item_id": archive, "parent_folder_id": watched}
        )
        check("the move succeeded", res.status_code == 200)
        events = client.take()
        into = seen_in(events, "folder_moved", ALICE, watched)
        out_of = seen_in(events, "folder_moved", ALICE, None)
        check("folder_moved arrived", into is not None)
        check("the folder is in its new parent when the event arrives", archive in (into or set()))
        check(
            "the folder is gone from its old parent when the event arrives",
            out_of is not None and archive not in out_of,
        )

        print("move a folder from the shared root into a personal folder")
        res = await http.post(
            "/desktop/transfer",
            json={
                "item_id": film,
                "source": "shared",
                "destination": "personal",
                "parent_folder_id": watched,
                "move": True,
            },
        )
        check("the transfer succeeded", res.status_code == 200)
        events = client.take()
        entered = seen_in(events, "folder_created", ALICE, watched)
        left = seen_in(events, "folder_deleted", SHARED_OWNER_ID, None)
        check("the destination is told", entered is not None)
        check(
            "the folder is in the destination when that event arrives",
            film in (entered or set()),
        )
        check("the source is told", left is not None)
        check(
            "the folder is gone from the source when that event arrives",
            left is not None and film not in left,
        )

        print("a request that fails after recording an event")
        res = await http.post("/check/notify-then-fail")
        check("the request failed", res.status_code == 400)
        check("no event went out for the rolled-back change", client.take() == [])
        async with async_session_factory() as session:
            root = await FileRepository(session).get_folder(ALICE, None)
        check("the change itself was rolled back", "Doomed" not in {r.name for r in root})

    await engine.dispose()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for label in _failures:
            print(f"  - {label}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
