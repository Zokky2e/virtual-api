"""
Standalone check for TransferService against a throwaway DB and storage
root. Not a pytest suite (this project has none yet) — run it directly:

    python -m scripts.check_transfer

Covers the parts that are easy to get wrong and impossible to eyeball:
bytes actually relocating, a folder's whole subtree changing owner, and
ReconcileService not re-importing the files a move left behind.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base
from app.database.repositories import FileRepository
from app.exceptions import ConflictError, InvalidOperationError
from app.services.reconcile_service import ReconcileService
from app.services.transfer_service import TransferService
from app.storage.local_storage import LocalFileStorage

ALICE = "alice"
SHARED = "shared"

_failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        _failures.append(label)


async def _seed_file(repo, storage, owner, name, parent=None, body=b"hello"):
    key = f"users/{owner}/{name}"

    async def chunks():
        yield body

    size = await storage.save(key, chunks())
    return await repo.create_file(
        owner_id=owner,
        name=name,
        parent_folder_id=parent,
        type_=__import__("app.database.models", fromlist=["FileType"]).FileType.text,
        storage_key=key,
        size=size,
    )


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        storage = LocalFileStorage(root / "storage")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            repo = FileRepository(session)
            service = TransferService(repo, storage)

            # ---------- copy a file, personal -> shared ----------
            print("copy file personal -> shared")
            original = await _seed_file(repo, storage, ALICE, "report.txt")
            await session.flush()

            result = await service.transfer(
                item_id=original.id,
                source_owner_id=ALICE,
                destination_owner_id=SHARED,
                destination_parent_folder_id=None,
                move=False,
            )
            copied = result.record
            check("new record owned by shared", copied.owner_id == SHARED)
            check("new record is a different row", copied.id != original.id)
            check("original still owned by alice", original.owner_id == ALICE)
            check(
                "copied bytes are under the shared prefix",
                copied.storage_key.startswith("users/shared/"),
            )
            check("copied bytes exist", await storage.exists(copied.storage_key))
            check("original bytes untouched", await storage.exists(original.storage_key))
            check("source not marked removed", result.removed_from_source is False)

            # ---------- move a file, personal -> shared ----------
            print("move file personal -> shared")
            movable = await _seed_file(repo, storage, ALICE, "clip.txt")
            await session.flush()
            old_key = movable.storage_key

            result = await service.transfer(
                item_id=movable.id,
                source_owner_id=ALICE,
                destination_owner_id=SHARED,
                destination_parent_folder_id=None,
                move=True,
            )
            moved = result.record
            check("same row, new owner", moved.id == movable.id and moved.owner_id == SHARED)
            check("storage key repointed", moved.storage_key != old_key)
            check("new bytes exist", await storage.exists(moved.storage_key))
            check("old bytes deleted", not await storage.exists(old_key))
            check("source marked removed", result.removed_from_source is True)
            check(
                "no longer visible to alice",
                await repo.get_by_id(ALICE, movable.id) is None,
            )

            # ---------- move a folder with contents ----------
            print("move folder personal -> shared")
            folder = await repo.create_folder(
                owner_id=ALICE, name="Trip", parent_folder_id=None
            )
            await session.flush()
            child = await _seed_file(repo, storage, ALICE, "photo.txt", parent=folder.id)
            nested = await repo.create_folder(
                owner_id=ALICE, name="Raw", parent_folder_id=folder.id
            )
            await session.flush()
            grandchild = await _seed_file(
                repo, storage, ALICE, "raw.txt", parent=nested.id
            )
            await session.flush()
            child_old_key = child.storage_key
            grandchild_old_key = grandchild.storage_key

            await service.transfer(
                item_id=folder.id,
                source_owner_id=ALICE,
                destination_owner_id=SHARED,
                destination_parent_folder_id=None,
                move=True,
            )
            check("folder re-owned", folder.owner_id == SHARED)
            check("direct child re-owned", child.owner_id == SHARED)
            check("nested folder re-owned", nested.owner_id == SHARED)
            check("grandchild re-owned", grandchild.owner_id == SHARED)
            check("child bytes relocated", not await storage.exists(child_old_key))
            check("grandchild bytes relocated", not await storage.exists(grandchild_old_key))
            check("grandchild bytes present", await storage.exists(grandchild.storage_key))
            check("tree structure intact", grandchild.parent_folder_id == nested.id)

            # ---------- rejections ----------
            print("rejections")
            same_tree = await _seed_file(repo, storage, ALICE, "same.txt")
            await session.flush()
            try:
                await service.transfer(
                    item_id=same_tree.id,
                    source_owner_id=ALICE,
                    destination_owner_id=ALICE,
                    destination_parent_folder_id=None,
                    move=False,
                )
                check("same-tree transfer rejected", False)
            except InvalidOperationError:
                check("same-tree transfer rejected", True)

            folder_copy = await repo.create_folder(
                owner_id=ALICE, name="NoCopy", parent_folder_id=None
            )
            await session.flush()
            try:
                await service.transfer(
                    item_id=folder_copy.id,
                    source_owner_id=ALICE,
                    destination_owner_id=SHARED,
                    destination_parent_folder_id=None,
                    move=False,
                )
                check("folder copy rejected", False)
            except InvalidOperationError:
                check("folder copy rejected", True)

            clash = await _seed_file(repo, storage, ALICE, "report.txt", body=b"other")
            await session.flush()
            try:
                await service.transfer(
                    item_id=clash.id,
                    source_owner_id=ALICE,
                    destination_owner_id=SHARED,
                    destination_parent_folder_id=None,
                    move=False,
                )
                check("name clash rejected", False)
            except ConflictError:
                check("name clash rejected", True)

            await session.commit()

        # ---------- reconcile must not resurrect moved files ----------
        print("reconcile after move")
        async with session_factory() as session:
            repo = FileRepository(session)
            reconcile = ReconcileService(repo, root / "storage")
            created = await reconcile.reconcile_user(ALICE)
            names = sorted(r.name for r in created)
            check(
                f"alice gains no duplicates from moved files (got {names})",
                all(n not in ("clip.txt", "photo.txt", "raw.txt") for n in names),
            )
            await session.commit()

        await engine.dispose()

    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
