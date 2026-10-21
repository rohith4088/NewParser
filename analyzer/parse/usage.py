"""Parser for node and pod resource usage snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import BinaryIO, Iterator


@dataclass
class MetricFact:
    ts_micros: int | None
    node: str
    metric_name: str
    metric_value: float
    unit: str
    detail: str

    def to_tuple(self) -> tuple:
        return (
            self.ts_micros,
            self.node,
            self.metric_name,
            self.metric_value,
            self.unit,
            self.detail,
        )


_DF_LINE = re.compile(
    r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(.+)$"
)


def parse_usage_stream(
    stream: BinaryIO, filename: str, node: str = "node-1"
) -> Iterator[MetricFact]:
    """Parse text command outputs like df, free, top into metric entries."""
    content = stream.read().decode("utf-8", errors="replace")
    lower_fn = filename.lower()

    # 1. Disk usage (df output)
    if "disk_usage" in lower_fn or "df" in lower_fn:
        for line in content.splitlines():
            match = _DF_LINE.match(line.strip())
            if match:
                fs = match.group(1)
                percent = float(match.group(5))
                mount = match.group(6)
                yield MetricFact(
                    ts_micros=None,
                    node=node,
                    metric_name="disk_usage_percent",
                    metric_value=percent,
                    unit="%",
                    detail=f"Filesystem {fs} mounted on {mount}: {percent:g}% used",
                )

    # 2. Memory usage (free -m output)
    elif "memory_usage" in lower_fn or "free" in lower_fn:
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].lower().startswith("mem:"):
                try:
                    total = float(parts[1])
                    used = float(parts[2])
                    pct = round((used / total) * 100, 1) if total > 0 else 0.0
                    yield MetricFact(
                        ts_micros=None,
                        node=node,
                        metric_name="memory_usage_percent",
                        metric_value=pct,
                        unit="%",
                        detail=f"RAM: {used:.0f}MB / {total:.0f}MB ({pct}%)",
                    )
                except ValueError:
                    pass

    # 3. Kubectl top pods
    elif "kubectl_top_pods" in lower_fn:
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 3 and not parts[0].startswith("NAME"):
                # NAME CPU(cores) MEMORY(bytes)
                name = parts[0]
                cpu_str = parts[1]
                mem_str = parts[2]
                yield MetricFact(
                    ts_micros=None,
                    node=node,
                    metric_name="pod_resource_usage",
                    metric_value=0.0,
                    unit="",
                    detail=f"Pod {name}: CPU={cpu_str}, Memory={mem_str}",
                )
