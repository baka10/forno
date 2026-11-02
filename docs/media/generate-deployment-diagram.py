#!/usr/bin/env python3
"""Regenerates deployment-topology.drawio(.png) from the repo's docker-compose.yml.

Parses docker-compose.yml with the drawio-skill's composeimports importer, groups
services by their compose `profiles:` entry (core / observability / sim / load —
see docker-compose.yml's own comment for what each profile is), swaps the noisy
`build: .` label for each forno-image service's actual `command:`, and lays the
result out with Graphviz via autolayout.py.

Usage (from the repo root):
    python3 docs/media/generate-deployment-diagram.py \
        --skill-dir /path/to/drawio-skill/skills/drawio-skill
    drawio -x -f png -e -o docs/media/deployment-topology.drawio.png \
        docs/media/deployment-topology.drawio

The --skill-dir default assumes the drawio-skill checkout used to author these
diagrams; pass your own path if it lives elsewhere. See docs/media/README.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
OUTPUT_DRAWIO = REPO_ROOT / "docs" / "media" / "deployment-topology.drawio"

# docker-compose.yml's `profiles:` value per service — kept here rather than
# re-parsed, since composeimports.py's --group only groups by network, and this
# repo's services share one (default) network.
PROFILE_OF = {
    "postgres": "core",
    "redpanda": "core",
    "migrate": "core",
    "api": "core",
    "outbox-relay": "core",
    "projector-kitchen": "core",
    "projector-stats": "core",
    "prometheus": "observability",
    "grafana": "observability",
    "alertmanager": "observability",
    "redpanda-console": "observability",
    "pgadmin": "observability",
    "simulator": "sim",
}

# Real command per forno-image service, replacing the uninformative `build: .`
# composeimports.py emits for every locally-built service.
COMMAND_OF = {
    "migrate": "command: migrate",
    "api": "command: serve --reload",
    "outbox-relay": "command: relay",
    "projector-kitchen": "command: project kitchen",
    "projector-stats": "command: project stats",
    "simulator": "python -m simulator",
}

# Long registry paths that overflow autolayout's default 160px box width.
SHORT_LABEL = {
    "redpanda": ("redpanda\nredpandadata/redpanda:v24.2.18", 200),
    "redpanda-console": ("redpanda-console\nredpandadata/console:v2.8.2", 200),
    "alertmanager": ("alertmanager\nprom/alertmanager:v0.27.0", 180),
    "prometheus": ("prometheus\nprom/prometheus:v3.0.1", 180),
    "grafana": ("grafana\ngrafana/grafana:11.4.0", 180),
    "postgres": ("postgres\npostgres:16-alpine", 170),
}

VOLUME_STYLE = "shape=cylinder3;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;"


def build_graph(skill_dir: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(skill_dir / "scripts" / "composeimports.py"), str(COMPOSE_FILE)],
        capture_output=True,
        text=True,
        check=True,
    )
    graph = json.loads(result.stdout)

    for node in graph["nodes"]:
        node_id = node["id"]
        if node_id in PROFILE_OF:
            profile = PROFILE_OF[node_id]
            node["group"] = profile
            node["groupLabel"] = f"profile: {profile}"
        if node_id in COMMAND_OF:
            node["label"] = f"{node_id}\n{COMMAND_OF[node_id]}"
        if node_id in SHORT_LABEL:
            label, width = SHORT_LABEL[node_id]
            node["label"] = label
            node["width"] = width
        if node_id.startswith("vol:"):
            node["style"] = VOLUME_STYLE
            node["width"] = 100
            node["height"] = 60

    return graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill-dir",
        type=Path,
        required=True,
        help="path to drawio-skill's skills/drawio-skill directory (has scripts/composeimports.py)",
    )
    args = parser.parse_args()

    graph = build_graph(args.skill_dir)
    graph_path = REPO_ROOT / "docs" / "media" / "_deployment-graph.json"
    graph_path.write_text(json.dumps(graph, indent=2))

    subprocess.run(
        [
            sys.executable,
            str(args.skill_dir / "scripts" / "autolayout.py"),
            str(graph_path),
            "-o",
            str(OUTPUT_DRAWIO),
        ],
        check=True,
    )
    graph_path.unlink()
    print(f"wrote {OUTPUT_DRAWIO}")
    print(f"next: drawio -x -f png -e -o docs/media/deployment-topology.drawio.png {OUTPUT_DRAWIO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
