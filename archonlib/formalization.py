from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DECL_RE = re.compile(r"^\s*(theorem|lemma|example|def|abbrev|structure|class|inductive|instance)\b", re.M)
QD_RE = re.compile(r"(\\mathcal\{Q\}_d|\bQ_d\b|\bQd\b)")
RD_RE = re.compile(r"(\\mathcal\{R\}_d|\bR_d\b|\bRd\b)")
MONIC_RE = re.compile(r"(\bmonic\b|首一)", re.I)
EXACT_DEGREE_RE = re.compile(r"(次数恰好|exact degree|monic of degree|degree\s+d\b)", re.I)
SPECIAL_CASE_D_ZERO_RE = re.compile(r"(\bQ_0\b|\bd\s*=\s*0\b)", re.I)
QD_DEF_RE = re.compile(r"^\s*(def|abbrev)\s+Qd\b", re.M)
RD_DEF_RE = re.compile(r"^\s*(def|abbrev)\s+Rd\b", re.M)
MONIC_DECL_RE = re.compile(r"(\bMonic\b|\.Monic\b|\bmonic\b)")
EXACT_DEGREE_DECL_RE = re.compile(r"(natDegree\s*=\s*d\b|degree\s*=\s*d\b)", re.I)
SYMMETRIC_TRIPLE_RE = re.compile(r"Fin\s+3\s*→")
BOUNDED_DEGREE_RE = re.compile(r"(degreeLT|BoundedPoly|degree\s*<\s*d\b)", re.I)
OBJECTIVE_RE = re.compile(
    r"Informal objective:\s*(?P<body>.*?)(?:\n\s*Suggested Mathlib modules:|\n\s*Notes:|\n-\s*/|\Z)",
    re.S,
)

FORMALIZATION_FILENAME_SUFFIX = ".json"

REQUIRED_ITEM_DEFINE_QD = "define_qd"
REQUIRED_ITEM_DEFINE_RD = "define_rd"
REQUIRED_ITEM_MONIC = "encode_monic_constraint"
REQUIRED_ITEM_EXACT_DEGREE = "encode_exact_degree_constraint"
REQUIRED_ITEM_D_ZERO = "encode_d_zero_special_case"

FORBIDDEN_SIMPLIFICATION_DROP_MONIC = "drop_monic_constraint"
FORBIDDEN_SIMPLIFICATION_REPLACE_EXACT_DEGREE = "replace_exact_degree_with_lt"
FORBIDDEN_SIMPLIFICATION_SYMMETRIZE = "symmetrize_qd_rd"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _formalization_filename(rel_path: str) -> str:
    return rel_path.replace("/", "_") + FORMALIZATION_FILENAME_SUFFIX


def formalization_contract_path(workspace: Path, rel_path: str) -> Path:
    return workspace / ".archon" / "formalization" / _formalization_filename(rel_path)


def _workspace_source_root(workspace: Path) -> Path:
    candidate = workspace.parent / "source"
    if candidate.exists():
        return candidate
    return workspace


def _source_kind(source_text: str) -> str:
    return "theorem_header" if DECL_RE.search(source_text) else "comment_only"


def _normalize_required_items(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _bundle_paths(source_root: Path, rel_path: str) -> list[Path]:
    paths: list[Path] = []
    source_path = source_root / rel_path
    if source_path.exists():
        paths.append(source_path)

    for name in ("QUESTIONS.md", "README.md"):
        candidate = source_root / name
        if candidate.exists():
            paths.append(candidate)

    if source_path.exists():
        for parent in [source_path.parent, *source_path.parents]:
            if parent == source_root.parent:
                break
            for candidate in sorted(parent.glob("Extra*.md")):
                if candidate not in paths:
                    paths.append(candidate)
            if parent == source_root:
                break

    return paths


def _extract_informal_objective(text: str) -> str | None:
    match = OBJECTIVE_RE.search(text)
    if match is None:
        return None
    body = match.group("body").strip()
    return body or None


def _derive_required_items(bundle_text: str) -> list[str]:
    items: list[str] = []
    if QD_RE.search(bundle_text):
        items.append(REQUIRED_ITEM_DEFINE_QD)
    if RD_RE.search(bundle_text):
        items.append(REQUIRED_ITEM_DEFINE_RD)
    if MONIC_RE.search(bundle_text):
        items.append(REQUIRED_ITEM_MONIC)
    if EXACT_DEGREE_RE.search(bundle_text):
        items.append(REQUIRED_ITEM_EXACT_DEGREE)
    if SPECIAL_CASE_D_ZERO_RE.search(bundle_text):
        items.append(REQUIRED_ITEM_D_ZERO)
    return items


def _load_or_derive_contract(workspace: Path, rel_path: str) -> dict[str, Any]:
    contract_path = formalization_contract_path(workspace, rel_path)
    payload = _read_json(contract_path)
    if payload is not None:
        payload = dict(payload)
        payload["present"] = True
        payload["path"] = contract_path.relative_to(workspace).as_posix()
        payload["requiredItems"] = _normalize_required_items(payload.get("requiredItems"))
        payload["sourceBundlePaths"] = [
            str(item)
            for item in payload.get("sourceBundlePaths", [])
            if isinstance(item, str) and item.strip()
        ]
        return payload

    source_root = _workspace_source_root(workspace)
    source_path = source_root / rel_path
    source_text = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    source_kind = _source_kind(source_text)
    if source_kind != "comment_only":
        return {
            "present": False,
            "path": None,
            "sourceKind": source_kind,
            "sourceBundlePaths": [],
            "informalObjective": None,
            "requiredItems": [],
            "allowedDeferrals": [],
            "forbiddenSimplifications": [],
        }

    bundle_paths = _bundle_paths(source_root, rel_path)
    bundle_text = "\n\n".join(path.read_text(encoding="utf-8") for path in bundle_paths)
    return {
        "present": True,
        "path": None,
        "sourceKind": source_kind,
        "sourceBundlePaths": [
            path.relative_to(source_root).as_posix() if path.is_relative_to(source_root) else str(path)
            for path in bundle_paths
        ],
        "informalObjective": _extract_informal_objective(source_text),
        "requiredItems": _derive_required_items(bundle_text),
        "allowedDeferrals": [],
        "forbiddenSimplifications": [
            FORBIDDEN_SIMPLIFICATION_DROP_MONIC,
            FORBIDDEN_SIMPLIFICATION_REPLACE_EXACT_DEGREE,
            FORBIDDEN_SIMPLIFICATION_SYMMETRIZE,
        ],
    }


def _covers_required_item(workspace_text: str, item: str) -> bool:
    if item == REQUIRED_ITEM_DEFINE_QD:
        return QD_DEF_RE.search(workspace_text) is not None
    if item == REQUIRED_ITEM_DEFINE_RD:
        return RD_DEF_RE.search(workspace_text) is not None
    if item == REQUIRED_ITEM_MONIC:
        return MONIC_DECL_RE.search(workspace_text) is not None
    if item == REQUIRED_ITEM_EXACT_DEGREE:
        return EXACT_DEGREE_DECL_RE.search(workspace_text) is not None
    if item == REQUIRED_ITEM_D_ZERO:
        return SPECIAL_CASE_D_ZERO_RE.search(workspace_text) is not None
    return False


def _detected_forbidden_simplifications(workspace_text: str, required_items: list[str]) -> list[str]:
    forbidden: list[str] = []
    requires_monic = REQUIRED_ITEM_MONIC in required_items
    requires_exact_degree = REQUIRED_ITEM_EXACT_DEGREE in required_items
    requires_rd = REQUIRED_ITEM_DEFINE_RD in required_items
    has_monic = _covers_required_item(workspace_text, REQUIRED_ITEM_MONIC)
    has_exact_degree = _covers_required_item(workspace_text, REQUIRED_ITEM_EXACT_DEGREE)
    has_rd = _covers_required_item(workspace_text, REQUIRED_ITEM_DEFINE_RD)
    symmetric_triple = SYMMETRIC_TRIPLE_RE.search(workspace_text) is not None
    bounded_degree = BOUNDED_DEGREE_RE.search(workspace_text) is not None

    if requires_monic and not has_monic and (symmetric_triple or bounded_degree):
        forbidden.append(FORBIDDEN_SIMPLIFICATION_DROP_MONIC)
    if requires_exact_degree and not has_exact_degree and bounded_degree:
        forbidden.append(FORBIDDEN_SIMPLIFICATION_REPLACE_EXACT_DEGREE)
    if requires_rd and not has_rd and symmetric_triple:
        forbidden.append(FORBIDDEN_SIMPLIFICATION_SYMMETRIZE)
    return forbidden


def assess_formalization(workspace: Path, rel_path: str) -> dict[str, Any]:
    contract = _load_or_derive_contract(workspace, rel_path)
    source_kind = str(contract.get("sourceKind") or "theorem_header")
    if source_kind != "comment_only":
        return {
            "sourceKind": source_kind,
            "fidelity": "not_applicable",
            "contract": {
                "present": bool(contract.get("present")),
                "path": contract.get("path"),
                "sourceBundlePaths": contract.get("sourceBundlePaths", []),
                "requiredItems": contract.get("requiredItems", []),
                "unresolvedItems": [],
                "forbiddenSimplifications": [],
            },
        }

    workspace_path = workspace / rel_path
    workspace_text = workspace_path.read_text(encoding="utf-8") if workspace_path.exists() else ""
    required_items = _normalize_required_items(contract.get("requiredItems"))
    unresolved_items = [item for item in required_items if not _covers_required_item(workspace_text, item)]
    forbidden = _detected_forbidden_simplifications(workspace_text, required_items)

    fidelity = "preserved"
    if forbidden:
        fidelity = "violated"
    elif unresolved_items:
        fidelity = "partial"

    return {
        "sourceKind": source_kind,
        "fidelity": fidelity,
        "contract": {
            "present": bool(contract.get("present")),
            "path": contract.get("path"),
            "sourceBundlePaths": contract.get("sourceBundlePaths", []),
            "requiredItems": required_items,
            "unresolvedItems": unresolved_items,
            "forbiddenSimplifications": forbidden,
            "informalObjective": contract.get("informalObjective"),
        },
    }
