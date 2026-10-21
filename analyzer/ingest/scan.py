"""Scan a bundle: walk it, classify every file, and build the coverage manifest.

Only the first few KB of each file are read, so a scan is cheap relative to a
full parse. It is also the first thing the UI runs after upload.
"""

from __future__ import annotations

import os
from typing import Callable

from .archive import ArchiveSafetyError, Issue, Limits, Member, walk
from .classifier import classify
from .manifest import Manifest

HEAD_BYTES = 4096

MemberCallback = Callable[[Member, "object"], None]


def scan(
    source: str | os.PathLike,
    limits: Limits | None = None,
    path_filter: Callable[[str, bool], bool] | None = None,
    on_member: MemberCallback | None = None,
) -> Manifest:
    """Walk ``source`` and return its coverage manifest.

    ``on_member(member, classification)`` is called for each readable file
    while its stream is still valid; later phases use it to parse in the same
    pass instead of reading the bundle twice.
    """
    manifest = Manifest(source=str(source))
    for event in walk(source, limits, path_filter=path_filter):
        if isinstance(event, Issue):
            manifest.add_issue(event)
            continue
        member = event
        try:
            head = member.head(HEAD_BYTES)
        except ArchiveSafetyError as exc:
            manifest.add_issue(Issue(member.path, exc.kind, str(exc), scope="archive"))
            continue
        except Exception as exc:  # per-file isolation: one bad file never stops a scan
            classification = classify(member.path, b"", size=member.size)
            manifest.add_member(member.path, classification, member.size)
            manifest.add_read_failure(member.path, classification, f"unreadable: {exc}")
            continue

        classification = classify(member.path, head, member.size, member.is_archive)
        manifest.add_member(member.path, classification, member.size)
        if on_member is not None:
            try:
                on_member(member, classification)
            except Exception as exc:
                manifest.add_read_failure(member.path, classification, f"read failed: {exc}")
    return manifest
