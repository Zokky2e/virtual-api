# virtual-api (backend)

This is the **backend half** of a two-repo system. The **frontend half lives
in a sibling folder**: `../virtual_desktop` (Flutter Web + Windows). When a
task touches request/response shapes, auth flow, or WebSocket event
contracts, read the relevant Flutter-side files too — the two repos must
agree on those shapes exactly, and only the client's
`ApiFileSystemRepository`/`ApiStorageService`/`ApiWebSocketClient` know how
responses get deserialized.

`../virtual_desktop/CLAUDE.md` has the frontend-side equivalent of this file.

## What this service is

A FastAPI server that is the single source of truth for the Virtual Desktop
app's files: it exposes folder/file CRUD, uploads, downloads, HTTP
Range-based video/audio streaming, and a WebSocket for live update
notifications, backed by a local filesystem on a (self-hosted, Ubuntu)
server. The Flutter client never talks to a filesystem or SSHes in — REST +
WebSocket only.

## Tech stack

- FastAPI (`app/main.py`), Uvicorn
- Pydantic v2 + `pydantic-settings` for config (`app/config.py`, reads `.env`)
- SQLAlchemy 2.x async + `aiosqlite` for metadata (file/folder records) —
  see `app/database/`
- Alembic present in `requirements.txt` for migrations (check
  `database/migrations/` for current state before assuming schema changes
  need a manual migration vs. relying on `init_db()`'s create-if-missing
  behavior, which is explicitly called out as dev-only in `main.py`)
- `firebase-admin` — **verification of client-supplied Firebase ID tokens
  only**. This server never stores or handles user passwords.
- `aiofiles` for async local file I/O
- `python-multipart` for upload parsing

Check `requirements.txt` for exact version floors before assuming an API.

## Folder structure (actual, under `app/`)

```
app/
  main.py              # create_app(), CORS, lifespan (init_db), entrypoint
  config.py            # Settings (env/.env): firebase_project_id, storage_root,
                        #   database_path, cors_allow_origins, etc. — single source
                        #   of config, don't read os.environ elsewhere
  constants.py         # SHARED_OWNER_ID = "shared", etc.
  exceptions.py        # register_exception_handlers(app)
  dependencies.py
  api/
    router.py          # mounts every router below onto api_router
    health.py           -> GET /health
    folders.py           -> /desktop (list, create, search, recycle-bin)
    files.py             -> /desktop (upload, download, rename, move, delete)
    streaming.py          -> /desktop (HTTP Range video/audio streaming)
    shared.py             -> /desktop/shared/* — mirrors folders.py+files.py+
                              streaming.py, but scoped to SHARED_OWNER_ID
                              instead of the caller's own uid
    transfers.py          -> POST /desktop/transfer — the one route that
                              touches both trees (move/copy across them)
    wallpapers.py         -> /desktop/wallpapers/* — desktop wallpapers, which
                              are NOT file-tree items: own table, own storage
                              prefix, no WebSocket events
    websocket.py          -> /ws (outside the /desktop REST prefix)
    dependencies.py       # FastAPI Depends() providers: get_folder_service, etc.
  auth/
    dependencies.py      # get_current_user / get_current_user_header_or_query
    firebase.py          # firebase-admin token verification
    models.py            # AuthUser
  database/
    database.py          # engine/session setup, init_db()
    models.py             # SQLAlchemy models (FileRecord, WallpaperRecord)
    repositories.py       # DB-level CRUD used by services/ (FileRepository,
                          #   WallpaperRepository)
    migrations/
  services/               # business logic layer — routers depend on these, not on
                            # database/ or storage/ directly
    folder_service.py
    file_service.py
    stream_service.py
    transfer_service.py       # cross-tree move/copy: relocates bytes as well
                                # as re-owning rows, and walks a folder's subtree
    wallpaper_service.py      # wallpaper upload/list/select + range streaming
    notification_service.py   # WebSocket broadcast wrapper used by other services
    reconcile_service.py      # scans storage_root/users/<owner>/ for files with
                                # no DB record yet (out-of-band drops via scp etc.)
  storage/
    base.py              # StorageRepository interface (listFolder/upload/delete/
                          #   rename/move/open_range/...)
    local_storage.py     # current (only) implementation — LocalFileStorage,
                          #   confined to storage_root with a path-escape guard
  streaming/
    range.py             # HTTP Range header parsing -> ByteRange
    response.py           # builds 200 vs 206 StreamingResponse from a ByteRange
  websocket/
    manager.py            # ConnectionManager — tracks active sockets, broadcast_all()
    events.py             # event payload shapes (file_created, file_deleted, ...)
  schemas/                # Pydantic request/response models (FileResponse, etc.)
data/                     # SQLite DB file lives here (database_path)
storage/                  # physical file storage root (storage_root) — per-user
                            # subfolders plus storage/users/shared/ for the shared tree
scripts/
  reconcile_all.py        # same reconciliation logic as /desktop/shared/sync, run
                            # standalone via `python -m scripts.reconcile_all`
                            # (intended for cron/systemd-timer use, no HTTP/auth)
```

## Architecture rules to preserve

- **Routers depend on services, services depend on `StorageRepository` /
  DB repositories — never the other way, and routers never touch
  `storage/` or `database/` directly.** This mirrors the frontend's
  interface-first layering.
- **Metadata and file content are separate concerns.** File/folder rows in
  SQLite hold metadata (name, parent, owner, type, size, storage key);
  actual bytes live under `storage_root` via `StorageRepository`. Never put
  file bytes in the database.
- **Every "tree" (personal or shared) is scoped by `owner_id`.** The shared
  folder is not a special data model — it's the exact same `FolderService`/
  `FileService`/`StreamService` code path, addressed with the reserved
  sentinel `owner_id = SHARED_OWNER_ID` instead of a real Firebase UID, via
  a separate `shared.py` router. Don't special-case "is this shared?" logic
  inside the generic per-user routers (`files.py`/`folders.py`) — that's
  exactly what `shared.py` exists to avoid. The one operation that genuinely
  spans both trees, moving/copying an item across them, gets its own
  `transfers.py` for the same reason.
- **A cross-tree move relocates bytes, not just rows.** Storage keys are
  owner-prefixed, and `ReconcileService` treats any file under
  `users/<owner>/` that isn't in that owner's recorded keys as untracked. Move
  a row to another owner without moving its bytes and the next sync mints a
  duplicate record for the orphan. `scripts/check_transfer.py` covers this.
- **Streaming is `open_range()` + `StreamingResponse`, never full-file
  reads for video/audio.** `storage.open_range(key, start, end)` must stay
  an async generator so multi-GB files are never loaded into memory.
- **WebSocket module only connects/disconnects/broadcasts** — it does not
  reach into storage or the DB itself. Other services call
  `NotificationService` after a mutation completes; `NotificationService`
  talks to `websocket/manager.py`. Shared-tree mutations broadcast to
  *every* connected client (`broadcast_all`), not just the owning user's own
  sockets — that's intentional and differs from personal-tree events.
- **Auth is Firebase-ID-token verification only.** `get_current_user`
  gates "is this a logged-in Virtual Desktop user"; it does not encode
  per-owner access rules beyond that — ownership checks happen in the
  service layer (`get_item` raising `NotFoundError`/404 when a caller
  doesn't own an item, deliberately indistinguishable from "doesn't exist").

## API surface (summary — read the router files for exact shapes)

```
GET    /health

GET    /desktop                        list_root
GET    /desktop/folder/{folder_id}
POST   /desktop/folder
GET    /desktop/search?q=...
GET    /desktop/recycle-bin
POST   /desktop/recycle-bin/{item_id}/restore
DELETE /desktop/recycle-bin/{item_id}          # hard delete ("Delete Forever")

POST   /desktop/upload
GET    /desktop/download/{item_id}
GET    /desktop/stream/{item_id}               # HTTP Range aware
PATCH  /desktop/file/{item_id}                 # rename
PATCH  /desktop/move
DELETE /desktop/file/{item_id}                 # soft delete

/desktop/shared/*        # same shape as above, scoped to SHARED_OWNER_ID
POST   /desktop/shared/sync                    # reconciliation import
GET    /desktop/shared/recycle-bin             # shared tree's own bin
POST   /desktop/shared/recycle-bin/{id}/restore
DELETE /desktop/shared/recycle-bin/{id}        # hard delete

POST   /desktop/transfer                       # move/copy between the two trees

GET    /desktop/wallpapers                     # this user's wallpapers
POST   /desktop/wallpapers/upload
GET    /desktop/wallpapers/stream/{id}         # HTTP Range aware
GET    /desktop/wallpapers/{id}
PATCH  /desktop/wallpapers/{id}/active         # single-selection, clears the rest

WS     /ws                                     # outside the /desktop prefix
```

Every *file-tree item* response matches the same `FileResponse` schema
regardless of which namespace served it — the Flutter client's mapper is
shared between personal and shared trees, so don't introduce a shape
difference between `/desktop/*` and `/desktop/shared/*` for the same
resource type.

**Wallpapers are the one deliberate exception**, and are not a file tree.
They have their own table (`wallpapers`), their own schema
(`WallpaperResponse`), and — critically — their own storage prefix
`wallpapers/{owner_id}/` rather than `users/{owner_id}/`. That prefix is
what keeps `ReconcileService` from sweeping them into the file tree on the
next `POST /desktop/shared/sync`, so don't move wallpaper bytes under
`users/`. They also emit no WebSocket events: nothing in the file tree
changes when a wallpaper is uploaded, and pushing one would make every open
desktop re-list for a change it can't see.

## Browser-compatible video streaming (planned/partial — verify current state before assuming it's done)

Web playback goes through the browser's native `<video>` element
(`video_player` on the Flutter side), which only reliably plays H.264 video
+ AAC audio in an MP4 container. The design calls for the server to:
1. `ffprobe` uploaded media to detect codec/container,
2. remux (`-c copy`) if already H.264+AAC in a different container, or
   transcode (`libx264` + `aac`, `-movflags +faststart`) otherwise,
3. keep the original file untouched and store the browser-compatible
   version alongside it,
4. serve the compatible version to web clients from `/desktop/stream/{id}`
   while still allowing the original to be downloaded.

Check `app/services/`, `app/storage/`, and `scripts/` for whether this
ffmpeg pipeline is implemented yet, since the Windows desktop client
(`flutter_vlc_player`) doesn't need it — it decodes most codecs/containers
natively — so this may lag behind if desktop was the more recent focus. If
you're asked to touch upload or streaming and it's not implemented, treat it
as a known gap, not a bug.

## Run / dev commands

```
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Requires a `.env` (see `.env.example`) with at least `firebase_project_id`
and either `firebase_credentials_path` or ambient Application Default
Credentials. `storage_root` and `database_path` default to `./storage` and
`./data/virtual_desktop.db` respectively.

There is currently no automated test suite in this repo (no `tests/`
directory) — if you add one, `pytest` + `httpx.AsyncClient` against the
FastAPI app is the natural fit given the async stack.

## Deployment context

Intended to run behind Caddy on a self-hosted Ubuntu box, as a
systemd-managed Uvicorn process (per the docstring in `app/main.py`) —
not inside Docker at present per current planning docs, though check for
drift. `scripts/reconcile_all.py` is meant to run on a cron/systemd-timer
schedule independent of the API process.
