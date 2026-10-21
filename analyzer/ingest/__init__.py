"""Ingestion and discovery layer: archive streaming, content classification, and coverage manifest."""

from .archive import ArchiveSafetyError, Issue, Limits, Member, walk
from .classifier import (
    PRIMARY_SOURCES,
    Classification,
    SourceType,
    classify,
    classify_path,
    path_hints,
)
from .manifest import FAILED, FOUND, NOT_FOUND, PARTIAL, Manifest, SourceCoverage
from .scan import scan

__all__ = [
    "ArchiveSafetyError",
    "Issue",
    "Limits",
    "Member",
    "walk",
    "SourceType",
    "PRIMARY_SOURCES",
    "Classification",
    "classify",
    "classify_path",
    "path_hints",
    "Manifest",
    "SourceCoverage",
    "FOUND",
    "PARTIAL",
    "FAILED",
    "NOT_FOUND",
    "scan",
    "Inventory",
    "Unit",
    "Group",
    "Part",
    "build_inventory",
    "make_unit_path_filter",
]
from .inventory import (
    Group,
    Inventory,
    Part,
    Unit,
    build_inventory,
    make_unit_path_filter,
)
