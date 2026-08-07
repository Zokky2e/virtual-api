# scripts/reconcile_all.py
"""
Scans every user's storage folder and imports untracked files. Run
manually after scp'ing files in, or on a schedule via a systemd timer:

    python -m scripts.reconcile_all

Note: this runs outside the live server process, so it does NOT push
WebSocket notifications — the desktop will pick up new items next time
that folder is (re)loaded, not instantly. Use the /desktop/sync endpoint
instead if you want the live push.
"""

import asyncio

from app.config import get_settings
from app.database.database import async_session_factory
from app.database.repositories import FileRepository
from app.services.reconcile_service import ReconcileService
from app.constants import SHARED_OWNER_ID

async def main() -> None:
    settings = get_settings()
    users_dir = settings.storage_root / "users"
    if not users_dir.is_dir():
        print("No users directory yet — nothing to reconcile.")
        return

    async with async_session_factory() as session:
        repo = FileRepository(session)
        reconcile = ReconcileService(repo, settings.storage_root)
        total = 0
		# Shared folder first — same reconcile_user() call, just fixed owner_id.
        shared_created = await reconcile.reconcile_user(SHARED_OWNER_ID)
        if shared_created:
            print(f"shared: imported {len(shared_created)} file(s)")
            for record in shared_created:
                print(f"  - {record.name} ({record.id})")
        total += len(shared_created)
        
        for owner_dir in sorted(users_dir.iterdir()):
            if not owner_dir.is_dir():
                continue
            created = await reconcile.reconcile_user(owner_dir.name)
            if created:
                print(f"{owner_dir.name}: imported {len(created)} file(s)")
                for record in created:
                    print(f"  - {record.name} ({record.id})")
            total += len(created)
        await session.commit()
        print(f"Done. {total} file(s) imported total.")
	

if __name__ == "__main__":
    asyncio.run(main())