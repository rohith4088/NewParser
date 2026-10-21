"""Coverage manifest: what a bundle contained, and what could not be read.

A support bundle is a snapshot of a *failing* system, so sections are often
missing or damaged. The manifest makes that explicit. For every known source
type it records whether it was found, partially read, failed, or simply not
present, so a UI can say "no journal in this bundle" instead of showing an
empty list that looks like "no errors".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .archive import Issue
from .classifier import PRIMARY_SOURCES, Classification, SourceType, classify_path

# Keep memory bounded no matter how broken the bundle is.
MAX_RECORDED_ISSUES = 1000
SAMPLE_PATHS = 5

NOT_FOUND = "not_found"
FOUND = "found"
PARTIAL = "partial"
FAILED = "failed"


@dataclass
class SourceCoverage:
    files: int = 0
    failed_files: int = 0
    bytes: int = 0  # bytes whose size is known (compressed leaves have no size)
    sample_paths: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.files == 0:
            return NOT_FOUND
        if self.failed_files == 0:
            return FOUND
        if self.failed_files >= self.files:
            return FAILED
        return PARTIAL

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "files": self.files,
            "failed_files": self.failed_files,
            "bytes": self.bytes,
            "sample_paths": list(self.sample_paths),
        }


@dataclass
class Manifest:
    source: str = ""
    sources: dict[str, SourceCoverage] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    dropped_issues: int = 0
    nodes: set[str] = field(default_factory=set)
    namespaces: set[str] = field(default_factory=set)
    total_files: int = 0
    total_bytes: int = 0
    # False once any archive-level problem (truncation, bomb, ...) was seen.
    complete: bool = True

    def __post_init__(self) -> None:
        for source_type in PRIMARY_SOURCES:
            self.sources.setdefault(source_type.value, SourceCoverage())

    # -- recording ---------------------------------------------------------------

    def _bucket(self, source_type: SourceType) -> SourceCoverage:
        return self.sources.setdefault(source_type.value, SourceCoverage())

    def add_member(
        self, path: str, classification: Classification, size: int | None
    ) -> None:
        bucket = self._bucket(classification.source_type)
        bucket.files += 1
        self.total_files += 1
        if size:
            bucket.bytes += size
            self.total_bytes += size
        if len(bucket.sample_paths) < SAMPLE_PATHS:
            bucket.sample_paths.append(path)
        node = classification.hints.get("node")
        if node:
            self.nodes.add(node)
        namespace = classification.hints.get("namespace")
        if namespace and classification.source_type is SourceType.FLUENT:
            self.namespaces.add(namespace)

    def add_read_failure(
        self, path: str, classification: Classification, message: str
    ) -> None:
        """A file that was listed but could not be read afterwards."""
        self._bucket(classification.source_type).failed_files += 1
        self._record_issue(Issue(path, "corrupt", message))

    def add_issue(self, issue: Issue) -> None:
        if issue.scope == "archive":
            self.complete = False
        elif issue.kind in ("corrupt", "encrypted", "unsupported_compression"):
            # The file never reached ``add_member``; count it as present+failed
            # so the source status reflects the damage.
            source_type = classify_path(issue.path) or SourceType.TEXT
            bucket = self._bucket(source_type)
            if issue.kind != "unsupported_compression":
                bucket.files += 1
                self.total_files += 1
            bucket.failed_files += 1
        self._record_issue(issue)

    def _record_issue(self, issue: Issue) -> None:
        self.issue_counts[issue.kind] = self.issue_counts.get(issue.kind, 0) + 1
        if len(self.issues) < MAX_RECORDED_ISSUES:
            self.issues.append(issue)
        else:
            self.dropped_issues += 1

    # -- reading -----------------------------------------------------------------

    def status_of(self, source_type: SourceType | str) -> str:
        key = source_type.value if isinstance(source_type, SourceType) else source_type
        coverage = self.sources.get(key)
        return coverage.status if coverage else NOT_FOUND

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "complete": self.complete,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "nodes": sorted(self.nodes),
            "namespaces": sorted(self.namespaces),
            "sources": {name: cov.to_dict() for name, cov in self.sources.items()},
            "issue_counts": dict(self.issue_counts),
            "dropped_issues": self.dropped_issues,
            "issues": [
                {"path": i.path, "kind": i.kind, "message": i.message, "scope": i.scope}
                for i in self.issues
            ],
        }
