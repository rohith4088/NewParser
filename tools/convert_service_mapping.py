"""Convert ``service_mapping.md`` into declarative ``topology/services.yaml``.

The legacy parser regex-scanned the markdown at every run and kept the
numbering ("1. Application ...") in category names. This one-off generator
makes the mapping explicit data. After conversion the YAML is the source of
truth and can be edited by hand.

Usage:
    python tools/convert_service_mapping.py <service_mapping.md> <services.yaml>
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_BACKTICKED = re.compile(r"`([^`]+)`")
# "`a`, `b`: description"  (bullet body, without the leading "* ")
_BULLET_WITH_DESC = re.compile(r"^((?:`[^`]+`(?:,\s*)?)+):\s*(.*)$")
_PAREN_NOTE = re.compile(r"\((.*)\)\s*\.?\s*$")


@dataclass
class _Namespace:
    description: str = ""
    containers: list[str] = field(default_factory=list)
    container_descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class _Block:
    """Accumulates one ``###`` section until the next heading."""

    namespaces: list[str]
    description_lines: list[str] = field(default_factory=list)
    purpose: str = ""
    containers: list[str] = field(default_factory=list)
    container_descriptions: dict[str, str] = field(default_factory=dict)
    in_container_bullets: bool = False
    # Parenthetical on an inline ``**Containers:**`` line, e.g.
    # "`foo` (API gateway ...)". Used as the description when nothing else exists.
    inline_note: str = ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _clean_category_name(raw: str) -> str:
    # "1. Application Microservices & APIs" -> "Application Microservices & APIs"
    return re.sub(r"^\d+\.\s*", "", raw.strip())


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("*", "")).strip()


def convert(markdown: str) -> dict:
    categories: list[dict] = []
    current: dict | None = None
    block: _Block | None = None

    def finish_block() -> None:
        nonlocal block
        if block is None or current is None:
            block = None
            return
        description = _clean_text(" ".join(block.description_lines + [block.purpose]))
        if not description:
            description = block.inline_note
        for ns in block.namespaces:
            current["namespaces"][ns] = {
                "description": description,
                "containers": list(block.containers),
                **(
                    {"container_descriptions": dict(block.container_descriptions)}
                    if block.container_descriptions
                    else {}
                ),
            }
        block = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()

        if line.startswith("## "):
            finish_block()
            name = _clean_category_name(line[3:])
            current = {
                "id": _slug(name),
                "name": name,
                "description": "",
                "namespaces": {},
            }
            categories.append(current)
            continue

        if line.startswith("### "):
            finish_block()
            if current is None:
                raise ValueError("Namespace heading found before any category heading")
            block = _Block(namespaces=_BACKTICKED.findall(line))
            continue

        if current is None or line in ("", "---") or line.startswith("# "):
            continue

        if block is None:
            # Category intro paragraph.
            current["description"] = _clean_text(f"{current['description']} {line}")
            continue

        if line.startswith("* **Containers:**"):
            rest = line[len("* **Containers:**") :].strip()
            if rest:
                names = _BACKTICKED.findall(rest)
                block.containers.extend(names)
                note = _PAREN_NOTE.search(rest)
                if note:
                    block.inline_note = _clean_text(note.group(1))
                if note and len(names) == 1:
                    block.container_descriptions[names[0]] = _clean_text(note.group(1))
                block.in_container_bullets = False
            else:
                block.in_container_bullets = True
            continue

        if line.startswith("* **Purpose:**"):
            block.purpose = line[len("* **Purpose:**") :].strip()
            block.in_container_bullets = False
            continue

        if block.in_container_bullets and line.startswith("* "):
            body = line[2:].strip()
            match = _BULLET_WITH_DESC.match(body)
            if match:
                names = _BACKTICKED.findall(match.group(1))
                desc = _clean_text(match.group(2))
                for name in names:
                    block.containers.append(name)
                    if desc:
                        block.container_descriptions[name] = desc
            else:
                block.containers.extend(_BACKTICKED.findall(body))
            continue

        if not line.startswith("*"):
            block.description_lines.append(line)

    finish_block()
    return {
        "version": 1,
        "generated_from": "service_mapping.md",
        "categories": categories,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    source, target = Path(argv[1]), Path(argv[2])
    data = convert(source.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Service topology: namespace -> category, description and containers.\n"
        "# Generated once from service_mapping.md; this file is now the source of\n"
        "# truth and is meant to be edited by hand.\n"
    )
    target.write_text(
        header
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    namespaces = sum(len(c["namespaces"]) for c in data["categories"])
    print(f"Wrote {target}: {len(data['categories'])} categories, {namespaces} namespaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
