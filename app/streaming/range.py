"""
Parsing for the HTTP Range header (RFC 7233), used by the video-streaming
endpoint to serve partial content instead of the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass


class RangeParseError(Exception):
    """Malformed or unsatisfiable Range header."""


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive


def parse_range_header(range_header: str | None, file_size: int) -> ByteRange | None:
    """
    Parses a single-range `Range: bytes=start-end` header against a file
    of `file_size` bytes. Returns None if there's no Range header at all
    (caller should serve the full file with 200). Only single-range
    requests are supported — multi-range (`bytes=0-10,20-30`) is rare for
    video players and rejected rather than half-implemented.
    """
    if not range_header:
        return None

    if not range_header.startswith("bytes="):
        raise RangeParseError(f"Unsupported Range unit: {range_header!r}")

    spec = range_header[len("bytes=") :]
    if "," in spec:
        raise RangeParseError("Multi-range requests are not supported.")

    if "-" not in spec:
        raise RangeParseError(f"Malformed Range header: {range_header!r}")

    start_str, _, end_str = spec.partition("-")

    try:
        if start_str == "":
            # Suffix range: "bytes=-500" means "last 500 bytes".
            if end_str == "":
                raise ValueError
            suffix_length = int(end_str)
            if suffix_length <= 0:
                raise ValueError
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str != "" else file_size - 1
    except ValueError:
        raise RangeParseError(f"Malformed Range header: {range_header!r}") from None

    if start > end or start >= file_size or start < 0:
        raise RangeParseError(f"Range not satisfiable for file of size {file_size}.")

    end = min(end, file_size - 1)
    return ByteRange(start=start, end=end)