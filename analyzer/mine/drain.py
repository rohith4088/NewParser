"""Pure-Python online Drain log template mining algorithm.

Based on Drain (He et al., ICWS 2017):
"Drain: An Online Log Parsing Approach with Fixed Depth Tree".
Clusters unbounded log messages into structured templates with variables masked
(e.g., ``Connecting to <*> on port <*>: connection refused``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

# Common variable masks
_VARIABLE_PATTERNS = [
    (re.compile(r"0x[0-9a-fA-F]+"), "<*>"),  # Hex pointers
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<*>"),  # UUID
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b"), "<*>"),  # IP addresses and ip:port
    (re.compile(r"https?://\S+"), "<*>"),  # URLs
    (re.compile(r"(?<=\s)/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+"), "<*>"),  # File paths
    (re.compile(r"\b\d+\b"), "<*>"),  # Integers
]


def mask_variables(text: str) -> str:
    """Mask obvious dynamic variables before tokenizing."""
    s = text
    for pattern, replacement in _VARIABLE_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


@dataclass
class LogCluster:
    cluster_id: int
    log_template_tokens: list[str]
    size: int = 1
    sample: str = ""

    @property
    def template(self) -> str:
        return " ".join(self.log_template_tokens)


class Node:
    """Tree node in the fixed-depth Drain parse tree."""

    def __init__(self):
        self.children: dict[str, Node] = {}
        self.clusters: list[LogCluster] = []


class DrainParser:
    """Fixed-depth parse tree for fast log message template clustering."""

    def __init__(
        self,
        max_depth: int = 4,
        sim_th: float = 0.5,
        max_clusters: int = 2000,
    ):
        self.max_depth = max_depth
        self.sim_th = sim_th
        self.max_clusters = max_clusters
        self.root = Node()
        self.clusters: list[LogCluster] = []
        self._next_id = 1

    def match(self, message: str) -> LogCluster:
        """Parse or match a log message, returning its assigned LogCluster."""
        cleaned = mask_variables(message.strip())
        tokens = cleaned.split()
        if not tokens:
            tokens = ["<EMPTY>"]

        cluster = self._tree_search(self.root, tokens)
        if cluster is None:
            # Create new cluster
            if len(self.clusters) < self.max_clusters:
                cluster = LogCluster(
                    cluster_id=self._next_id,
                    log_template_tokens=list(tokens),
                    size=1,
                    sample=message[:200],
                )
                self._next_id += 1
                self.clusters.append(cluster)
                self._add_to_tree(self.root, cluster)
            else:
                # Fallback to nearest or generic
                cluster = self.clusters[0] if self.clusters else LogCluster(0, tokens, 1, message)
        else:
            # Update existing cluster template
            cluster.size += 1
            self._update_template(cluster, tokens)

        return cluster

    def _tree_search(self, root: Node, tokens: list[str]) -> LogCluster | None:
        seq_len = len(tokens)
        len_key = str(seq_len)

        if len_key not in root.children:
            return None

        curr_node = root.children[len_key]
        curr_depth = 1

        for token in tokens:
            if curr_depth >= self.max_depth:
                break
            # If token contains digits or wildcard, traverse wildcard child '*'
            if any(char.isdigit() for char in token) or token == "<*>":
                search_key = "*"
            else:
                search_key = token

            if search_key in curr_node.children:
                curr_node = curr_node.children[search_key]
            elif "*" in curr_node.children:
                curr_node = curr_node.children["*"]
            else:
                return None
            curr_depth += 1

        # Search candidates in leaf node
        return self._fast_match(curr_node.clusters, tokens)

    def _add_to_tree(self, root: Node, cluster: LogCluster) -> None:
        tokens = cluster.log_template_tokens
        seq_len = len(tokens)
        len_key = str(seq_len)

        if len_key not in root.children:
            root.children[len_key] = Node()
        curr_node = root.children[len_key]

        curr_depth = 1
        for token in tokens:
            if curr_depth >= self.max_depth:
                break
            if any(char.isdigit() for char in token) or token == "<*>":
                key = "*"
            else:
                key = token

            if key not in curr_node.children:
                curr_node.children[key] = Node()
            curr_node = curr_node.children[key]
            curr_depth += 1

        curr_node.clusters.append(cluster)

    def _fast_match(self, candidates: list[LogCluster], tokens: list[str]) -> LogCluster | None:
        max_sim = -1.0
        max_cluster: LogCluster | None = None

        for cluster in candidates:
            sim, num_params = self._seq_similarity(cluster.log_template_tokens, tokens)
            if sim > max_sim:
                max_sim = sim
                max_cluster = cluster

        if max_sim >= self.sim_th:
            return max_cluster
        return None

    def _seq_similarity(self, template_tokens: list[str], seq_tokens: list[str]) -> tuple[float, int]:
        if len(template_tokens) != len(seq_tokens):
            return 0.0, 0

        sim_tokens = 0
        num_params = 0

        for t_tok, s_tok in zip(template_tokens, seq_tokens):
            if t_tok == "<*>":
                num_params += 1
                continue
            if t_tok == s_tok:
                sim_tokens += 1

        sim = sim_tokens / len(template_tokens) if template_tokens else 0.0
        return sim, num_params

    def _update_template(self, cluster: LogCluster, tokens: list[str]) -> None:
        updated = []
        for t_tok, s_tok in zip(cluster.log_template_tokens, tokens):
            if t_tok == s_tok:
                updated.append(t_tok)
            else:
                updated.append("<*>")
        cluster.log_template_tokens = updated

    def top_templates(self, limit: int = 20) -> list[LogCluster]:
        """Return the top clusters sorted by frequency."""
        return sorted(self.clusters, key=lambda c: -c.size)[:limit]
