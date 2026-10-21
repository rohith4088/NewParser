"""Streaming, safe traversal of support bundles.

``walk()`` yields every file inside a bundle without ever unpacking it to disk.
It understands tar, zip and gzip/bzip2/xz layers, recursively (a ``.tar`` that
contains a ``.tar.gz`` that contains a ``.tar`` ...), and detects formats by
*content*, never by file extension.

Contract for callers
--------------------
``walk()`` is a generator that yields :class:`Member` and :class:`Issue`
objects. A ``Member.stream`` is only valid until the generator is advanced:
read what you need first (nested tar streams cannot be re-read). Errors raised
while *reading* a member's stream happen on the caller's side; the caller
should catch them per file. Errors in the container structure itself are
reported as ``Issue`` objects and the walk continues with whatever is left.

Safety
------
Archives are never extracted, so there is no write-path traversal, but member
names are still validated because they become display paths (and later
temporary file names). Absolute paths, ``..`` components and NUL bytes are
rejected. Links and device nodes are skipped. Decompression bombs are stopped
by a per-layer compression-ratio guard, plus limits on nesting depth, member
count and total decompressed bytes.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import re
import stat
import tarfile
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

GZIP_MAGIC = b"\x1f\x8b"
BZ2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")

_ARCHIVE_SUFFIXES = (
    ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
    ".tar", ".zip", ".gz", ".bz2", ".xz",
)
_ARCHIVE_NAME = re.compile(
    r"\.(tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz|tar|zip)$", re.IGNORECASE
)

# Peeking this many bytes is enough to see a tar header's "ustar" magic (offset 257).
_SNIFF = 512


class ArchiveSafetyError(Exception):
    """A safety limit (compression ratio, size, member count) was exceeded."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Limits:
    # How many archive/compression layers may be nested.
    max_depth: int = 8
    max_members: int = 2_000_000
    # Total bytes produced by decompressors across the whole walk.
    max_total_bytes: int = 512 * 1024**3
    # A decompression layer may not expand by more than this factor ...
    max_ratio: float = 1000.0
    # ... once it has produced at least this many bytes (so tiny files that
    # compress extremely well, like a few KB of zeros, don't trip the guard).
    ratio_min_bytes: int = 1024**3
    # Nested zip files need a seekable stream; they are spooled to a temp file
    # up to this size.
    max_nested_zip_bytes: int = 4 * 1024**3


@dataclass
class Issue:
    """Something that prevented a file or archive from being read normally."""

    path: str
    kind: str  # unsafe_path, link_skipped, special_skipped, corrupt, truncated,
    #            encrypted, unsupported_compression, depth_limit, member_limit,
    #            size_limit, ratio_limit
    message: str
    # "file": one file is affected. "archive": the rest of an archive is.
    scope: str = "file"


class Member:
    """One file found in the bundle."""

    def __init__(
        self,
        path: str,
        stream: BinaryIO,
        size: int | None = None,
        mtime: float | None = None,
        encoding: str | None = None,
        is_archive: bool = False,
    ):
        self.path = path
        self.stream = stream
        self.size = size
        self.mtime = mtime
        # "gzip"/"bz2"/"xz" when the stream is already transparently decompressed.
        self.encoding = encoding
        # True for archives deliberately left unexpanded (directory mode).
        self.is_archive = is_archive

    def head(self, n: int = 4096) -> bytes:
        """First ``n`` bytes without consuming them."""
        return peek(self.stream, n)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Member({self.path!r}, size={self.size}, encoding={self.encoding})"


# --- stream helpers -------------------------------------------------------------


class Peekable:
    """Wraps a non-seekable stream so its first bytes can be inspected."""

    def __init__(self, raw: BinaryIO):
        self._raw = raw
        self._buf = b""

    def peek(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._raw.read(n - len(self._buf))
            if not chunk:
                break
            self._buf += chunk
        return self._buf[:n]

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            data = self._buf + self._raw.read()
            self._buf = b""
            return data
        if self._buf:
            taken, self._buf = self._buf[:n], self._buf[n:]
            if len(taken) < n:
                taken += self._raw.read(n - len(taken))
            return taken
        return self._raw.read(n)

    def readline(self, size: int = -1) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            nl_pos = self._buf.find(b"\n")
            if nl_pos != -1:
                chunk = self._buf[: nl_pos + 1]
                self._buf = self._buf[nl_pos + 1 :]
                chunks.append(chunk)
                break
            if self._buf:
                chunks.append(self._buf)
                total += len(self._buf)
                self._buf = b""
                if 0 <= size <= total:
                    break
            block = self._raw.read(8192)
            if not block:
                break
            self._buf = block
        return b"".join(chunks)

    def __iter__(self) -> "Peekable":
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        close = getattr(self._raw, "close", None)
        if close:
            close()


def _ensure_peekable(stream):
    return stream if _is_seekable(stream) else Peekable(stream)


def _is_seekable(stream) -> bool:
    fn = getattr(stream, "seekable", None)
    try:
        return bool(fn()) if fn else False
    except (AttributeError, OSError, ValueError):
        return False


def peek(stream, n: int) -> bytes:
    if isinstance(stream, Peekable):
        return stream.peek(n)
    pos = stream.tell()
    data = stream.read(n)
    stream.seek(pos)
    return data


class _CountingReader:
    """Counts compressed bytes consumed by a decompressor."""

    def __init__(self, raw):
        self._raw = raw
        self.count = 0

    def read(self, n: int = -1) -> bytes:
        data = self._raw.read(n)
        self.count += len(data)
        return data

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False


class _Budget:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.total_out = 0
        self.members = 0

    def add_output(self, n: int) -> None:
        self.total_out += n
        if self.total_out > self.limits.max_total_bytes:
            raise ArchiveSafetyError(
                "size_limit",
                f"decompressed more than {self.limits.max_total_bytes} bytes in total",
            )

    def add_member(self) -> None:
        self.members += 1
        if self.members > self.limits.max_members:
            raise ArchiveSafetyError(
                "member_limit", f"more than {self.limits.max_members} files in bundle"
            )


class _GuardedStream:
    """Decompressed stream that enforces the ratio and total-size limits."""

    def __init__(self, inner, counter: _CountingReader, budget: _Budget):
        self._inner = inner
        self._counter = counter
        self._budget = budget
        self._out = 0

    def read(self, n: int = -1) -> bytes:
        data = self._inner.read(n)
        produced = len(data)
        if produced:
            self._out += produced
            self._budget.add_output(produced)
            limits = self._budget.limits
            if self._out >= limits.ratio_min_bytes:
                consumed = max(self._counter.count, 1)
                if self._out > limits.max_ratio * consumed:
                    raise ArchiveSafetyError(
                        "ratio_limit",
                        f"decompression ratio {self._out / consumed:.0f}:1 exceeds "
                        f"{limits.max_ratio:.0f}:1",
                    )
        return data

    def readline(self, size: int = -1) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = self.read(1)
            if not chunk:
                break
            chunks.append(chunk)
            total += 1
            if chunk == b"\n" or (0 <= size <= total):
                break
        return b"".join(chunks)

    def __iter__(self) -> "_GuardedStream":
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close:
            close()


# --- format detection -----------------------------------------------------------


def sanitize_path(name: str) -> str | None:
    """Normalize an archive member name, or ``None`` if it is unsafe."""
    if "\x00" in name:
        return None
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts: list[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or None


def strip_archive_suffix(path: str) -> str:
    """``a/b.tar.gz`` -> ``a/b`` (used as the folder name for nested members)."""
    lower = path.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return path[: -len(suffix)]
    return path


def _compression_of(head: bytes) -> str | None:
    if head.startswith(GZIP_MAGIC):
        return "gzip"
    if head.startswith(BZ2_MAGIC):
        return "bz2"
    if head.startswith(XZ_MAGIC):
        return "xz"
    return None


def _is_tar(head: bytes) -> bool:
    return len(head) >= 262 and head[257:262] == b"ustar"


def _is_zip(head: bytes) -> bool:
    return head.startswith(ZIP_MAGICS)


def _join(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


# --- walking --------------------------------------------------------------------


def walk(
    source: str | os.PathLike,
    limits: Limits | None = None,
    path_filter: Callable[[str, bool], bool] | None = None,
) -> Iterator[Member | Issue]:
    """Yield every file in ``source`` (an archive file or a directory)."""
    limits = limits or Limits()
    budget = _Budget(limits)
    source = Path(source)
    if source.is_dir():
        yield from _walk_dir(source, limits, budget, path_filter=path_filter)
        return
    try:
        fh = open(source, "rb")
    except OSError as exc:
        yield Issue(str(source), "corrupt", f"cannot open: {exc}", scope="archive")
        return
    with fh:
        yield from _expand(fh, source.name, 0, limits, budget, top=True, path_filter=path_filter)


def _walk_dir(
    root: Path,
    limits: Limits,
    budget: _Budget,
    path_filter: Callable[[str, bool], bool] | None = None,
) -> Iterator[Member | Issue]:
    """Walk an already-extracted bundle.

    Archives found inside are *not* expanded: a directory that holds both
    ``x.tar`` and its extracted contents would otherwise be counted twice.
    Compressed log files (``.gz``) are still decompressed transparently.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                yield Issue(rel, "link_skipped", "symbolic link not followed")
                continue
            is_archive = bool(_ARCHIVE_NAME.search(filename))
            if path_filter is not None and not path_filter(rel, is_archive):
                continue
            try:
                st = full.stat()
                if not stat.S_ISREG(st.st_mode):
                    yield Issue(rel, "special_skipped", "not a regular file")
                    continue
                budget.add_member()
                fh = open(full, "rb")
            except ArchiveSafetyError as exc:
                yield Issue(rel, exc.kind, str(exc), scope="archive")
                return
            except OSError as exc:
                yield Issue(rel, "corrupt", f"cannot open: {exc}")
                continue
            with fh:
                if is_archive:
                    yield Member(rel, fh, size=st.st_size, mtime=st.st_mtime, is_archive=True)
                else:
                    yield from _expand(
                        fh,
                        rel,
                        1,
                        limits,
                        budget,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        path_filter=path_filter,
                    )


def _expand(
    stream,
    path: str,
    depth: int,
    limits: Limits,
    budget: _Budget,
    size: int | None = None,
    mtime: float | None = None,
    top: bool = False,
    path_filter: Callable[[str, bool], bool] | None = None,
) -> Iterator[Member | Issue]:
    """Decide what ``stream`` is (archive, compressed, plain) and recurse."""
    stream = _ensure_peekable(stream)
    try:
        head = peek(stream, _SNIFF)
    except (OSError, EOFError, zlib.error, lzma.LZMAError, ArchiveSafetyError) as exc:
        yield Issue(path, "corrupt", f"unreadable: {exc}")
        return

    compression = _compression_of(head)
    if compression:
        if depth >= limits.max_depth:
            yield Issue(path, "depth_limit", "nesting too deep; left as-is")
            yield Member(path, stream, size=size, mtime=mtime)
            return
        counter = _CountingReader(stream)
        try:
            if compression == "gzip":
                inner = gzip.GzipFile(fileobj=counter, mode="rb")
            elif compression == "bz2":
                inner = bz2.BZ2File(counter, "rb")
            else:
                inner = lzma.LZMAFile(counter, "rb")
            guarded = Peekable(_GuardedStream(inner, counter, budget))
            inner_head = guarded.peek(_SNIFF)
        except ArchiveSafetyError as exc:
            yield Issue(path, exc.kind, str(exc), scope="archive")
            return
        except (OSError, EOFError, zlib.error, lzma.LZMAError) as exc:
            yield Issue(path, "corrupt", f"bad {compression} data: {exc}")
            return

        if _is_tar(inner_head) or _is_zip(inner_head) or _compression_of(inner_head):
            # Compression wrapper around an archive (or more compression): keep
            # the name; the archive layer derives its folder name from it.
            yield from _expand(
                guarded, path, depth + 1, limits, budget, top=top, path_filter=path_filter
            )
        else:
            yield Member(path, guarded, size=None, mtime=mtime, encoding=compression)
        return

    if head.startswith(ZSTD_MAGIC):
        yield Issue(path, "unsupported_compression", "zstd is not supported; skipped")
        yield Member(path, stream, size=size, mtime=mtime)
        return

    if _is_tar(head):
        if depth >= limits.max_depth:
            yield Issue(path, "depth_limit", "nesting too deep; left as-is")
            yield Member(path, stream, size=size, mtime=mtime)
            return
        prefix = "" if top else strip_archive_suffix(path)
        yield from _walk_tar(stream, prefix, depth, limits, budget, path_filter=path_filter)
        return

    if _is_zip(head):
        if depth >= limits.max_depth:
            yield Issue(path, "depth_limit", "nesting too deep; left as-is")
            yield Member(path, stream, size=size, mtime=mtime)
            return
        prefix = "" if top else strip_archive_suffix(path)
        yield from _walk_zip(stream, prefix, depth, limits, budget, path_filter=path_filter)
        return

    yield Member(path, stream, size=size, mtime=mtime)


def _walk_tar(
    stream,
    prefix: str,
    depth: int,
    limits: Limits,
    budget: _Budget,
    path_filter: Callable[[str, bool], bool] | None = None,
) -> Iterator[Member | Issue]:
    # A seekable plain tar can skip over members cheaply; anything else must be
    # read strictly sequentially.
    mode = "r:" if _is_seekable(stream) else "r|"
    try:
        tf = tarfile.open(fileobj=stream, mode=mode)
    except ArchiveSafetyError as exc:
        yield Issue(prefix or "<archive>", exc.kind, str(exc), scope="archive")
        return
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        yield Issue(prefix or "<archive>", "corrupt", f"cannot open tar: {exc}", scope="archive")
        return
    with tf:
        try:
            for info in tf:
                if info.isdir():
                    continue
                name = sanitize_path(info.name)
                if name is None:
                    yield Issue(info.name, "unsafe_path", "unsafe member name; skipped")
                    continue
                path = _join(prefix, name)
                if info.issym() or info.islnk():
                    yield Issue(path, "link_skipped", "link not followed")
                    continue
                if not info.isreg():
                    yield Issue(path, "special_skipped", "not a regular file")
                    continue

                is_archive = any(info.name.lower().endswith(s) for s in _ARCHIVE_SUFFIXES)
                if path_filter is not None and not path_filter(path, is_archive):
                    continue

                budget.add_member()
                member_stream = tf.extractfile(info)
                if member_stream is None:  # pragma: no cover - isreg() guards this
                    continue
                yield from _expand(
                    member_stream,
                    path,
                    depth + 1,
                    limits,
                    budget,
                    size=info.size,
                    mtime=info.mtime,
                    path_filter=path_filter,
                )
        except ArchiveSafetyError as exc:
            yield Issue(prefix or "<archive>", exc.kind, str(exc), scope="archive")
        except (tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError) as exc:
            yield Issue(
                prefix or "<archive>",
                "truncated",
                f"archive ended unexpectedly: {exc}",
                scope="archive",
            )


def _walk_zip(
    stream,
    prefix: str,
    depth: int,
    limits: Limits,
    budget: _Budget,
    path_filter: Callable[[str, bool], bool] | None = None,
) -> Iterator[Member | Issue]:
    spooled = None
    try:
        if not _is_seekable(stream):
            # zipfile needs random access; spool nested zips to a temp file.
            spooled = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
            copied = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > limits.max_nested_zip_bytes:
                    yield Issue(
                        prefix or "<archive>",
                        "size_limit",
                        "nested zip too large to open",
                        scope="archive",
                    )
                    return
                spooled.write(chunk)
            spooled.seek(0)
            zip_stream = spooled
        else:
            zip_stream = stream

        try:
            zf = zipfile.ZipFile(zip_stream)
        except ArchiveSafetyError as exc:
            yield Issue(prefix or "<archive>", exc.kind, str(exc), scope="archive")
            return
        except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as exc:
            yield Issue(prefix or "<archive>", "corrupt", f"cannot open zip: {exc}", scope="archive")
            return

        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = sanitize_path(info.filename)
                if name is None:
                    yield Issue(info.filename, "unsafe_path", "unsafe member name; skipped")
                    continue
                path = _join(prefix, name)
                mode = info.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    yield Issue(path, "link_skipped", "link not followed")
                    continue
                if info.flag_bits & 0x1:
                    yield Issue(path, "encrypted", "encrypted zip member; skipped")
                    continue
                if (
                    info.file_size >= limits.ratio_min_bytes
                    and info.file_size > limits.max_ratio * max(info.compress_size, 1)
                ):
                    yield Issue(path, "ratio_limit", "suspicious compression ratio; skipped")
                    continue

                is_archive = any(info.filename.lower().endswith(s) for s in _ARCHIVE_SUFFIXES)
                if path_filter is not None and not path_filter(path, is_archive):
                    continue

                try:
                    budget.add_member()
                    member_stream = zf.open(info)
                except ArchiveSafetyError as exc:
                    yield Issue(prefix or "<archive>", exc.kind, str(exc), scope="archive")
                    return
                except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, zlib.error) as exc:
                    yield Issue(path, "corrupt", f"cannot open zip member: {exc}")
                    continue
                yield from _expand(
                    member_stream,
                    path,
                    depth + 1,
                    limits,
                    budget,
                    size=info.file_size,
                    mtime=None,
                    path_filter=path_filter,
                )
    finally:
        if spooled is not None:
            spooled.close()
