# app/constants.py
"""
Reserved sentinel values shared across the app. SHARED_OWNER_ID is not a
real Firebase UID (those are ~28 alphanumeric chars from Firebase Auth) —
it's used as the owner_id for the one shared folder every authenticated
user can see and write to. Because FileRepository/FolderService/
FileService are already owner_id-scoped for everything, "shared" just
slots in as if it were another user's tree; no new tables, no new
storage layout, no separate reconcile path.
scp movie.mp4 you@server:/srv/virtual-desktop/storage/users/shared/
"""

SHARED_OWNER_ID = "shared"