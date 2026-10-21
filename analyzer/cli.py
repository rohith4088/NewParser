"""Command line entry point.

Exposes CLI commands:
- ``--version``: package version
- ``topology``: inspect and validate service topology
- ``scan``: stream/walk a bundle, discover topology & file types, report coverage manifest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .analyze.rules import RuleEngine
from .config import Config, ConfigError
from .ingest import PRIMARY_SOURCES, FAILED, FOUND, NOT_FOUND, PARTIAL, build_inventory, scan
from .parse.pipeline import parse_bundle_to_store
from .parse.timestamps import micros_to_iso
from .store.duck import SessionStore
from .topology import Topology, TopologyError


def _cmd_topology(config: Config) -> int:
    try:
        topology = Topology.load(config.topology_path)
    except TopologyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not topology.known_namespaces():
        print(f"warning: no topology loaded from {config.topology_path}", file=sys.stderr)
    for category_id, name in topology.categories():
        namespaces = sorted(
            ns for ns in topology.known_namespaces() if topology.get(ns).category_id == category_id
        )
        print(f"{name} ({len(namespaces)})")
        for ns in namespaces:
            print(f"  - {ns}")
    return 0


def _cmd_scan(path_str: str, as_json: bool = False) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"error: path {path_str!r} does not exist", file=sys.stderr)
        return 1

    console = Console()
    with console.status(f"[bold cyan]Scanning bundle {path.name}..."):
        manifest = scan(path)

    if as_json:
        print(json.dumps(manifest.to_dict(), indent=2))
        return 0

    console.print(f"\n[bold green]Scan Complete:[/bold green] {path.name}")
    console.print(f"  [bold]Files Discovered:[/bold] {manifest.total_files:,}")
    console.print(f"  [bold]Archive Integrity:[/bold] {'[green]Complete[/green]' if manifest.complete else '[yellow]Partial / Truncated[/yellow]'}")
    if manifest.nodes:
        console.print(f"  [bold]Nodes Discovered:[/bold] {', '.join(sorted(manifest.nodes))}")
    if manifest.namespaces:
        console.print(f"  [bold]Namespaces Discovered ({len(manifest.namespaces)}):[/bold] {', '.join(sorted(manifest.namespaces))}")

    # Coverage Table
    table = Table(title="Bundle Coverage by Source Type", show_header=True, header_style="bold magenta")
    table.add_column("Source Type", style="dim", width=16)
    table.add_column("Status", width=12)
    table.add_column("Files", justify="right", width=10)
    table.add_column("Failed", justify="right", width=10)
    table.add_column("Sample Paths", style="cyan")

    status_styles = {
        FOUND: "[green]FOUND[/green]",
        PARTIAL: "[yellow]PARTIAL[/yellow]",
        FAILED: "[red]FAILED[/red]",
        NOT_FOUND: "[dim]NOT FOUND[/dim]",
    }

    for source_type in PRIMARY_SOURCES:
        cov = manifest.sources.get(source_type.value)
        if cov is None:
            continue
        status_str = status_styles.get(cov.status, cov.status)
        samples = ", ".join(cov.sample_paths[:2])
        if len(cov.sample_paths) > 2:
            samples += f" (+{len(cov.sample_paths) - 2} more)"
        table.add_row(
            source_type.value,
            status_str,
            f"{cov.files:,}" if cov.files else "-",
            f"{cov.failed_files:,}" if cov.failed_files else "-",
            samples or "-",
        )

    console.print()
    console.print(table)

    if manifest.issues:
        console.print(f"\n[yellow]Encountered {len(manifest.issues)} issues/warnings:[/yellow]")
        for issue in manifest.issues[:10]:
            console.print(f"  • [{issue.kind}] {issue.path}: {issue.message}")
        if len(manifest.issues) > 10:
            console.print(f"  ... and {len(manifest.issues) - 10} more issues.")

    console.print()
    return 0


def _cmd_inventory(path_str: str, as_json: bool = False, config: Config | None = None) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"error: path {path_str!r} does not exist", file=sys.stderr)
        return 1

    topology = None
    if config and config.topology_path and config.topology_path.exists():
        try:
            topology = Topology.load(config.topology_path)
        except Exception:
            topology = None

    console = Console()
    with console.status(f"[bold cyan]Inspecting bundle inventory for {path.name}..."):
        inv = build_inventory(path, topology=topology)

    if as_json:
        print(json.dumps(inv.to_dict(), indent=2))
        return 0

    console.print(f"\n[bold green]Inventory Discovered:[/bold green] {path.name}")
    console.print(f"  [bold]Total Parts:[/bold] {len(inv.parts)}")
    console.print(f"  [bold]Total Units:[/bold] {len(inv.get_all_units())}")

    for part in inv.parts:
        table = Table(
            title=f"Part: {part.name} ({part.id})",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Unit ID", style="bold", width=32)
        table.add_column("Group", width=18)
        table.add_column("Name", width=24)
        table.add_column("Files", justify="right", width=8)
        table.add_column("Size (MB)", justify="right", width=10)
        table.add_column("Date Range", style="dim", width=24)
        table.add_column("Description / Notes", style="dim")

        for group in part.groups:
            for u in group.units:
                size_mb = round(u.total_bytes / (1024 * 1024), 2)
                size_str = f"{size_mb} MB" if size_mb >= 0.1 else f"{u.total_bytes / 1024:.1f} KB"
                date_str = f"{u.date_min}..{u.date_max}" if u.date_min else "-"
                notes = u.description or (f"Namespace: {u.namespace}" if u.namespace else "-")
                table.add_row(
                    u.id,
                    group.name,
                    u.name,
                    f"{u.file_count:,}",
                    size_str,
                    date_str,
                    notes,
                )
        console.print()
        console.print(table)

    console.print()
    return 0


def _cmd_analyze(
    path_str: str,
    db_path_str: str | None = None,
    session_id: str | None = None,
    as_json: bool = False,
    units: list[str] | None = None,
    include_namespaces: list[str] | None = None,
    config: Config | None = None,
) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"error: path {path_str!r} does not exist", file=sys.stderr)
        return 1

    stem = path.name
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".tgz"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break

    sess_id = session_id or stem
    if db_path_str:
        db_path = Path(db_path_str)
    else:
        sessions_dir = (config.data_dir if config else Path("data")) / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        db_path = sessions_dir / f"{sess_id}.duckdb"

    topology = None
    if config and config.topology_path and config.topology_path.exists():
        try:
            topology = Topology.load(config.topology_path)
        except Exception:
            topology = None

    console = Console()
    with console.status(f"[bold cyan]Parsing bundle {path.name} into DuckDB store..."):
        with SessionStore(db_path) as store:
            inv = build_inventory(path, topology=topology)
            manifest = parse_bundle_to_store(
                path,
                store,
                session_id=sess_id,
                units=units,
                include_namespaces=include_namespaces,
                inventory=inv,
            )
            meta = store.get_metadata() or {}
            levels = store.count_by_level()
            namespaces = store.count_by_namespace()
            k8s_facts = store.get_k8s_facts()

            rule_engine = RuleEngine(config.rules_dir if config else None)
            findings = rule_engine.evaluate(store)

    if as_json:
        out = {
            "metadata": meta,
            "levels": levels,
            "namespaces": namespaces,
            "k8s_facts": k8s_facts,
            "findings": [f.to_dict() for f in findings],
            "manifest": manifest.to_dict(),
            "database": str(db_path),
        }
        print(json.dumps(out, indent=2))
        return 0

    console.print(f"\n[bold green]Analysis Complete:[/bold green] {path.name}")
    console.print(f"  [bold]Database Store:[/bold] {db_path}")
    console.print(f"  [bold]Total Files Scanned:[/bold] {manifest.total_files:,}")
    console.print(f"  [bold]Total Events Stored:[/bold] {meta.get('total_events', 0):,}")

    min_ts = meta.get("min_ts_micros")
    max_ts = meta.get("max_ts_micros")
    if min_ts and max_ts:
        console.print(f"  [bold]Time Window:[/bold] {micros_to_iso(min_ts)}  -->  {micros_to_iso(max_ts)}")

    # Findings Table
    if findings:
        f_table = Table(title=f"Root Cause Findings & Triage ({len(findings)})", show_header=True, header_style="bold red")
        f_table.add_column("Severity", width=12)
        f_table.add_column("Finding", style="bold", width=36)
        f_table.add_column("Category", width=24)
        f_table.add_column("Count", justify="right", width=8)
        f_table.add_column("Remediation", style="dim")
        for f in findings:
            sev_style = "red" if f.severity in ("CRITICAL", "FATAL") else ("yellow" if f.severity == "HIGH" else "cyan")
            f_table.add_row(
                f"[{sev_style}]{f.severity}[/{sev_style}]",
                f.title,
                f.category,
                f"{f.count:,}",
                f.remediation[:80] + ("..." if len(f.remediation) > 80 else ""),
            )
        console.print()
        console.print(f_table)

    # Level counts table
    lvl_table = Table(title="Event Counts by Level", show_header=True, header_style="bold magenta")
    lvl_table.add_column("Level", width=12)
    lvl_table.add_column("Count", justify="right", width=12)
    for lvl in ("FATAL", "ERROR", "WARN", "INFO", "DEBUG"):
        if lvl in levels:
            style = "red" if lvl in ("FATAL", "ERROR") else ("yellow" if lvl == "WARN" else "cyan")
            lvl_table.add_row(f"[{style}]{lvl}[/{style}]", f"{levels[lvl]:,}")
    console.print()
    console.print(lvl_table)

    # Top Namespaces
    if namespaces:
        ns_table = Table(title="Top Namespaces by Log Volume", show_header=True, header_style="bold magenta")
        ns_table.add_column("Namespace", width=25)
        ns_table.add_column("Events", justify="right", width=12)
        for ns, count in list(namespaces.items())[:8]:
            ns_table.add_row(ns, f"{count:,}")
        console.print()
        console.print(ns_table)

    # Kubernetes Facts
    if k8s_facts:
        k8s_table = Table(title=f"Critical Kubernetes Facts ({len(k8s_facts)})", show_header=True, header_style="bold red")
        k8s_table.add_column("Namespace", width=16)
        k8s_table.add_column("Resource", width=28)
        k8s_table.add_column("Reason", width=18)
        k8s_table.add_column("Detail", style="dim")
        for f in k8s_facts[:10]:
            k8s_table.add_row(f["namespace"], f["name"], f["reason"], f["detail"])
        console.print()
        console.print(k8s_table)

    console.print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analyzer", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"analyzer {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("topology", help="Validate and print the service topology")

    scan_parser = sub.add_parser("scan", help="Stream/scan a bundle or directory and print coverage manifest")
    scan_parser.add_argument("path", help="Path to archive (.tar, .tar.gz, .zip) or extracted directory")
    scan_parser.add_argument("--json", action="store_true", help="Output coverage manifest as JSON")

    inventory_parser = sub.add_parser("inventory", help="Inspect and display bundle parts, groups, and units without full parse")
    inventory_parser.add_argument("path", help="Path to archive (.tar, .tar.gz, .zip) or extracted directory")
    inventory_parser.add_argument("--json", action="store_true", help="Output inventory as JSON")

    analyze_parser = sub.add_parser("analyze", help="Parse bundle into embedded DuckDB session and report summary")
    analyze_parser.add_argument("path", help="Path to archive or extracted directory")
    analyze_parser.add_argument("--db", default=None, help="Path to output DuckDB database file")
    analyze_parser.add_argument("--session", default=None, help="Session identifier")
    analyze_parser.add_argument("--unit", action="append", default=None, help="Unit ID to parse (can be specified multiple times)")
    analyze_parser.add_argument("--namespace", action="append", default=None, help="Namespace to parse (can be specified multiple times)")
    analyze_parser.add_argument("--json", action="store_true", help="Output analysis summary as JSON")

    serve_parser = sub.add_parser("serve", help="Launch local air-gapped web dashboard")
    serve_parser.add_argument("--port", type=int, default=None, help="Port to bind (default: 8080)")
    serve_parser.add_argument("--host", default=None, help="Host to bind (default: 127.0.0.1)")

    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "topology":
        return _cmd_topology(config)
    elif args.command == "scan":
        return _cmd_scan(args.path, as_json=args.json)
    elif args.command == "inventory":
        return _cmd_inventory(args.path, as_json=args.json, config=config)
    elif args.command == "analyze":
        return _cmd_analyze(
            args.path,
            db_path_str=args.db,
            session_id=args.session,
            as_json=args.json,
            units=args.unit,
            include_namespaces=args.namespace,
            config=config,
        )
    elif args.command == "serve":
        cfg = config
        if args.port or args.host:
            cfg = Config(
                data_dir=config.data_dir,
                bundles_dir=config.bundles_dir,
                topology_path=config.topology_path,
                rules_dir=config.rules_dir,
                host=args.host or config.host,
                port=args.port or config.port,
                max_upload_bytes=config.max_upload_bytes,
            )
        from .server import run_server
        run_server(cfg)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
