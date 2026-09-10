"""
Standalone check for TransferService against a throwaway DB and storage
root. Not a pytest suite (this project has none yet) — run it directly:

    python -m scripts.check_transfer

Covers the parts that are easy to get wrong and impossible to eyeball:
bytes actually relocating, a folder's whole subtree changing owner, a move
renaming bytes rather than rewriting them, same-named files in one folder
keeping their own bytes, and ReconcileService not re-importing the files a
move left behind.
"""

from __future__ import annotations

import asyncio
import errno
import tempfile
from pathlib import Path
from unittest import mock

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


async def _seed_file(
    repo, storage, owner, name, parent=None, body=b"hello", key=None
):
    key = key or f"users/{owner}/{name}"

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

            # ---------- a move renames bytes, it doesn't rewrite them ----------
            print("move renames rather than rewrites")
            film = await _seed_file(
                repo, storage, ALICE, "film.mkv", body=b"frame" * 1024
            )
            await session.flush()
            inode_before = (root / "storage" / film.storage_key).stat().st_ino

            await service.transfer(
                item_id=film.id,
                source_owner_id=ALICE,
                destination_owner_id=SHARED,
                destination_parent_folder_id=None,
                move=True,
            )
            moved_path = root / "storage" / film.storage_key
            check("moved bytes present", moved_path.is_file())
            check(
                "same file on disk, renamed rather than rewritten",
                moved_path.is_file() and moved_path.stat().st_ino == inode_before,
            )

            # ---------- same-named files, same millisecond ----------
            print("move folder whose files share a name")
            films = await repo.create_folder(
                owner_id=ALICE, name="Films", parent_folder_id=None
            )
            await session.flush()
            first = await repo.create_folder(
                owner_id=ALICE, name="First", parent_folder_id=films.id
            )
            second = await repo.create_folder(
                owner_id=ALICE, name="Second", parent_folder_id=films.id
            )
            await session.flush()
            first_subs = await _seed_file(
                repo, storage, ALICE, "subs.srt", parent=first.id,
                body=b"first", key="users/alice/1_subs.srt",
            )
            second_subs = await _seed_file(
                repo, storage, ALICE, "subs.srt", parent=second.id,
                body=b"second", key="users/alice/2_subs.srt",
            )
            await session.flush()

            # Pinned clock: a folder's files move back to back, and on a fast
            # disk two of them share a millisecond without any help.
            with mock.patch("app.services.transfer_service.time") as clock:
                clock.time.return_value = 1_700_000_000.0
                await service.transfer(
                    item_id=films.id,
                    source_owner_id=ALICE,
                    destination_owner_id=SHARED,
                    destination_parent_folder_id=None,
                    move=True,
                )
            check(
                "same-named files get separate keys",
                first_subs.storage_key != second_subs.storage_key,
            )
            check(
                "first file's bytes intact",
                await storage.read(first_subs.storage_key) == b"first",
            )
            check(
                "second file's bytes intact",
                await storage.read(second_subs.storage_key) == b"second",
            )

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
                all(
                    n not in ("clip.txt", "photo.txt", "raw.txt", "film.mkv", "subs.srt")
                    for n in names
                ),
            )
            await session.commit()

        # ---------- a move that can't rename falls back to copy + delete ----------
        print("move across disks")

        async def far_body():
            yield b"far away"

        await storage.save("users/alice/far.bin", far_body())
        with mock.patch(
            "aiofiles.os.replace",
            side_effect=OSError(errno.EXDEV, "Invalid cross-device link"),
        ):
            await storage.move("users/alice/far.bin", "users/shared/far.bin")
        check(
            "bytes arrive at the destination",
            await storage.read("users/shared/far.bin") == b"far away",
        )
        check("and leave the source", not await storage.exists("users/alice/far.bin"))

        await engine.dispose()

    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
