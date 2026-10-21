import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

from analyzer.config import PROJECT_ROOT, Config
from analyzer.topology import UNMAPPED_CATEGORY, Topology, TopologyError

LEGACY_MD = PROJECT_ROOT.parent / "service_mapping.md"
BUNDLE_FLUENT_DIR = (
    PROJECT_ROOT.parent
    / "extracted_logs"
    / "cluster-logs"
    / "cluster-logs"
    / "fluentbit-logs"
    / "fluent-logs"
)


def _load_converter():
    path = PROJECT_ROOT / "tools" / "convert_service_mapping.py"
    spec = importlib.util.spec_from_file_location("convert_service_mapping", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def topology() -> Topology:
    return Topology.load(Config().topology_path)


# --- the shipped services.yaml -------------------------------------------------


def test_shipped_topology_loads_with_four_categories(topology):
    assert len(topology.categories()) == 4
    names = [name for _, name in topology.categories()]
    # The legacy parser left the "1. " numbering in category names.
    assert all(not re.match(r"^\d", name) for name in names)


def test_category_lookup(topology):
    assert topology.category_of("ccs-system") == "Application Microservices & APIs"
    assert topology.category_of("kube-system") == "Core Infrastructure & Platform"
    assert topology.category_of("kafka-ccs") == "Event Streaming & Messaging"
    assert topology.category_of("cassandra-ccs") == "Databases & Data Stores"


def test_multi_namespace_headings_share_data(topology):
    # "### `cassandra` & `cassandra-ccs`" must map both namespaces.
    a, b = topology.get("cassandra"), topology.get("cassandra-ccs")
    assert a and b
    assert a.containers == b.containers
    assert a.description == b.description


def test_descriptions_and_containers(topology):
    assert "Cloud Control Service" in topology.describe("ccs-system")
    assert "account-management" in topology.get("ccs-system").containers
    assert "TLS" in topology.container_description(
        "onprem", "certificate-management"
    )
    # Parenthetical-only sections fall back to the note as description.
    assert "API Gateway" in topology.describe("unified-api")
    assert topology.describe("ccs-pingfederate")


def test_no_namespace_has_empty_description_or_containers(topology):
    for ns in topology.known_namespaces():
        info = topology.get(ns)
        assert info.description, f"{ns} has no description"
        assert info.containers, f"{ns} has no containers"


def test_yaml_matches_every_namespace_in_markdown(topology):
    if not LEGACY_MD.exists():
        pytest.skip("service_mapping.md not present")
    in_markdown = set()
    for line in LEGACY_MD.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("### "):
            in_markdown.update(re.findall(r"`([^`]+)`", line))
    assert topology.known_namespaces() == in_markdown


@pytest.mark.skipif(not BUNDLE_FLUENT_DIR.is_dir(), reason="extracted bundle not present")
def test_every_namespace_in_the_sample_bundle_is_mapped(topology):
    observed = [p.name for p in BUNDLE_FLUENT_DIR.iterdir() if p.is_dir()]
    assert observed, "expected namespaces in the sample bundle"
    assert topology.unmapped(observed) == []


# --- behaviour for bundles that differ from the sample -------------------------


def test_unknown_namespace_falls_into_other(topology):
    assert topology.category_of("totally-new-ns") == UNMAPPED_CATEGORY
    assert topology.describe("totally-new-ns") == ""
    assert topology.get("totally-new-ns") is None
    assert topology.container_description("totally-new-ns", "x") == ""


def test_unmapped_lists_only_unknown(topology):
    assert topology.unmapped(["ccs-system", "zzz", "aaa", "zzz"]) == ["aaa", "zzz"]


def test_missing_file_gives_empty_topology(tmp_path):
    empty = Topology.load(tmp_path / "nope.yaml")
    assert empty.known_namespaces() == set()
    assert empty.category_of("ccs-system") == UNMAPPED_CATEGORY


def test_empty_file_gives_error_for_missing_categories(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("version: 1\n")
    with pytest.raises(TopologyError):
        Topology.load(path)


def test_invalid_yaml_rejected(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("categories: [unclosed\n")
    with pytest.raises(TopologyError):
        Topology.load(path)


def test_duplicate_namespace_rejected(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "categories": [
                    {"id": "a", "name": "A", "namespaces": {"x": {}}},
                    {"id": "b", "name": "B", "namespaces": {"x": {}}},
                ]
            }
        )
    )
    with pytest.raises(TopologyError):
        Topology.load(path)


def test_category_without_id_rejected(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump({"categories": [{"name": "A"}]}))
    with pytest.raises(TopologyError):
        Topology.load(path)


# --- converter ----------------------------------------------------------------


def test_converter_handles_the_three_markdown_shapes():
    converter = _load_converter()
    md = "\n".join(
        [
            "# Title",
            "## 1. First Group",
            "Intro paragraph.",
            "### `alpha`",
            "Alpha description.",
            "* **Containers:**",
            "  * `a1`, `a2`: Does a thing.",
            "  * `a3`",
            "### `beta` & `gamma`",
            "* **Containers:** `b1`, `b2`",
            "* **Purpose:** Shared purpose.",
            "### `delta`",
            "* **Containers:** `d1` (Only a note).",
            "---",
            "## 2. Second Group",
            "### `eps`",
            "* **Containers:** `e1`",
        ]
    )
    data = converter.convert(md)
    first, second = data["categories"]
    assert first["name"] == "First Group"
    assert first["description"] == "Intro paragraph."
    alpha = first["namespaces"]["alpha"]
    assert alpha["description"] == "Alpha description."
    assert alpha["containers"] == ["a1", "a2", "a3"]
    assert alpha["container_descriptions"] == {"a1": "Does a thing.", "a2": "Does a thing."}
    assert first["namespaces"]["beta"] == first["namespaces"]["gamma"]
    assert first["namespaces"]["beta"]["description"] == "Shared purpose."
    delta = first["namespaces"]["delta"]
    assert delta["description"] == "Only a note"
    assert delta["containers"] == ["d1"]
    assert second["namespaces"]["eps"]["containers"] == ["e1"]


def test_converter_rejects_namespace_before_category():
    converter = _load_converter()
    with pytest.raises(ValueError):
        converter.convert("### `orphan`\n")
