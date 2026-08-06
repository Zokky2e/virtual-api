"""
Application-level exceptions and the handlers that turn them into HTTP
responses. Services/repositories raise these (or the storage-layer
exceptions from app.storage.base); routers never construct HTTPException
directly — that keeps HTTP status codes out of the business logic and in
one place.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.storage.base import StorageError, StorageNotFoundError, StoragePathError


class AppError(Exception):
    """Base class for all application-level errors."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    """A file/folder id (or storage key) doesn't exist, or doesn't belong
    to the requesting user — deliberately returns 404 either way so
    ownership can't be probed by ID enumeration."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """E.g. creating a folder/file whose name already exists in the
    destination folder."""

    status_code = status.HTTP_409_CONFLICT


class InvalidOperationError(AppError):
    """E.g. moving a folder into its own descendant, deleting a
    non-empty folder without a recursive flag, malformed Range header."""

    status_code = status.HTTP_400_BAD_REQUEST


def _error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


def register_exception_handlers(app: FastAPI) -> None:
    """Call once from main.py during app setup."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.message, exc.status_code)

    # Storage-layer exceptions map onto the same shape so routers never
    # need to know whether a failure came from the DB or the filesystem.
    @app.exception_handler(StorageNotFoundError)
    async def handle_storage_not_found(
        _: Request, exc: StorageNotFoundError
    ) -> JSONResponse:
        return _error_response(str(exc), status.HTTP_404_NOT_FOUND)

    @app.exception_handler(StoragePathError)
    async def handle_storage_path_error(
        _: Request, exc: StoragePathError
    ) -> JSONResponse:
        return _error_response(str(exc), status.HTTP_400_BAD_REQUEST)

    @app.exception_handler(StorageError)
    async def handle_storage_error(_: Request, exc: StorageError) -> JSONResponse:
        return _error_response(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)