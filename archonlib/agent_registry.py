from __future__ import annotations

import json
from pathlib import Path


REQUIRED_KEYS = {
    "id",
    "status",
    "kind",
    "summary",
    "reads",
    "writes",
    "outputs",
    "handoff_to",
    "observability",
}


def canonical_agent_registry_dir(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[1]
    return base / "agents"


def _load_agent_payload(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = REQUIRED_KEYS.difference(payload)
    if missing:
        missing_rendered = ", ".join(sorted(missing))
        raise ValueError(f"{path} is missing required keys: {missing_rendered}")
    for key in ("reads", "writes", "outputs", "handoff_to", "observability"):
        if not isinstance(payload.get(key), list):
            raise ValueError(f"{path} field {key!r} must be a list")
    return payload


def load_agent_registry(root: Path | None = None) -> list[dict[str, object]]:
    registry_dir = canonical_agent_registry_dir(root)
    files = sorted(registry_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no agent registry files found under {registry_dir}")
    return [_load_agent_payload(path) for path in files]


def load_agent_contracts(root: Path | None = None) -> list[dict[str, object]]:
    return load_agent_registry(root)


def load_agent_registry_map(root: Path | None = None) -> dict[str, dict[str, object]]:
    payloads = load_agent_registry(root)
    registry: dict[str, dict[str, object]] = {}
    for payload in payloads:
        agent_id = payload["id"]
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent registry entry must contain a non-empty string id")
        registry[agent_id] = payload
    return registry


def load_agent_contract_map(root: Path | None = None) -> dict[str, dict[str, object]]:
    return load_agent_registry_map(root)
