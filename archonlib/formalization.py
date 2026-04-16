from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


DECL_RE = re.compile(r"^\s*(theorem|lemma|example|def|abbrev|structure|class|inductive|instance)\b", re.M)
QD_RE = re.compile(r"(\\mathcal\{Q\}_d|\bQ_d\b|\bQd\b)")
RD_RE = re.compile(r"(\\mathcal\{R\}_d|\bR_d\b|\bRd\b)")
MONIC_RE = re.compile(r"(\bmonic\b|首一)", re.I)
EXACT_DEGREE_RE = re.compile(r"(次数恰好|exact degree|monic of degree|degree\s+d\b)", re.I)
SPECIAL_CASE_D_ZERO_RE = re.compile(r"(\bQ_0\b|\bd\s*=\s*0\b)", re.I)
FINITE_CARDINALITY_SOURCE_RE = re.compile(r"(有限集|基数|cardinality|finite set|finiteness|count)", re.I)
QD_DEF_RE = re.compile(r"^\s*(def|abbrev)\s+Qd\b", re.M)
RD_DEF_RE = re.compile(r"^\s*(def|abbrev)\s+Rd\b", re.M)
MONIC_DECL_RE = re.compile(r"(\bMonic\b|\.Monic\b|\bmonic\b)")
EXACT_DEGREE_DECL_RE = re.compile(r"(natDegree\s*=\s*d\b|degree\s*=\s*d\b)", re.I)
FINITE_CARDINALITY_DECL_RE = re.compile(
    r"^\s*(theorem|lemma|instance)\b[\s\S]{0,1200}?(Finite\b|Fintype\.card\b|Nat\.card\b|Fintype\s*\(|Finite\s*\()",
    re.M,
)
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
REQUIRED_ITEM_FINITENESS_CARDINALITY = "state_finiteness_and_cardinality"

FORBIDDEN_SIMPLIFICATION_DROP_MONIC = "drop_monic_constraint"
FORBIDDEN_SIMPLIFICATION_REPLACE_EXACT_DEGREE = "replace_exact_degree_with_lt"
FORBIDDEN_SIMPLIFICATION_SYMMETRIZE = "symmetrize_qd_rd"

DEFAULT_REQUIRED_ITEM_DETAILS = {
    REQUIRED_ITEM_DEFINE_QD: "Define Q_d in the declaration layer and keep its distinguished component faithful.",
    REQUIRED_ITEM_DEFINE_RD: "Define R_d in the declaration layer and keep its distinguished component faithful.",
    REQUIRED_ITEM_MONIC: "Preserve the monic constraint on the distinguished component instead of dropping it.",
    REQUIRED_ITEM_EXACT_DEGREE: "Preserve the exact-degree condition instead of replacing it with a degree-< d surrogate.",
    REQUIRED_ITEM_D_ZERO: "Preserve the explicit d = 0 / Q_0 special case from the source.",
    REQUIRED_ITEM_FINITENESS_CARDINALITY: "State the finiteness/Fintype and cardinality theorems promised by the informal objective.",
}

DEFAULT_FORBIDDEN_SIMPLIFICATION_DETAILS = {
    FORBIDDEN_SIMPLIFICATION_DROP_MONIC: "Do not remove the monic requirement from the benchmark object.",
    FORBIDDEN_SIMPLIFICATION_REPLACE_EXACT_DEGREE: "Do not weaken an exact-degree object into a degree-< d surrogate.",
    FORBIDDEN_SIMPLIFICATION_SYMMETRIZE: "Do not collapse asymmetric source objects into a symmetric triple encoding.",
}


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


def _route_note_slug(rel_path: str) -> str:
    stem = rel_path[:-5] if rel_path.endswith(".lean") else rel_path
    slug = stem.replace("/", "-")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-")
    return slug or "objective"


def autoformalization_route_note_path(workspace: Path, rel_path: str) -> Path:
    return workspace / ".archon" / "informal" / f"{_route_note_slug(rel_path)}-autoformalize.md"


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


def _normalize_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["requiredItems"] = _normalize_required_items(normalized.get("requiredItems"))
    normalized["sourceBundlePaths"] = [
        str(item)
        for item in normalized.get("sourceBundlePaths", [])
        if isinstance(item, str) and item.strip()
    ]
    allowed_deferrals = normalized.get("allowedDeferrals")
    normalized["allowedDeferrals"] = [
        str(item)
        for item in allowed_deferrals
        if isinstance(item, str) and item.strip()
    ] if isinstance(allowed_deferrals, list) else []
    forbidden = normalized.get("forbiddenSimplifications")
    normalized["forbiddenSimplifications"] = [
        str(item)
        for item in forbidden
        if isinstance(item, str) and item.strip()
    ] if isinstance(forbidden, list) else []
    helper_notes = normalized.get("helperNotes")
    normalized["helperNotes"] = [
        str(item)
        for item in helper_notes
        if isinstance(item, str) and item.strip()
    ] if isinstance(helper_notes, list) else []
    return normalized


def _detail_text(details: object, key: str) -> str | None:
    if not isinstance(details, Mapping):
        return None
    value = details.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_string_list(items: object) -> list[str]:
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


def _autoformalization_first_edit_steps(contract: Mapping[str, Any]) -> list[str]:
    required_items = set(_normalize_required_items(contract.get("requiredItems")))
    steps: list[str] = [
        "After you read the contract and one route note, stop gathering and write the smallest faithful declaration layer immediately.",
    ]
    if REQUIRED_ITEM_DEFINE_QD in required_items or REQUIRED_ITEM_DEFINE_RD in required_items:
        steps.append(
            "Start with a helper type for degree-`< d` polynomials, for example `↥(Polynomial.degreeLT F d)`."
        )
    if REQUIRED_ITEM_MONIC in required_items or REQUIRED_ITEM_EXACT_DEGREE in required_items:
        steps.append(
            "Add a separate subtype for the distinguished monic exact-degree-`d` component instead of weakening it to another bounded-degree slot."
        )
    if REQUIRED_ITEM_D_ZERO in required_items:
        steps.append("Encode the explicit `Q0 = {(1, 0, 0)}` source special case before treating the general `d ≥ 1` family as complete.")
    if REQUIRED_ITEM_DEFINE_QD in required_items:
        steps.append("Define `Q_d` from exactly one monic exact-degree component and two bounded-degree components.")
    if REQUIRED_ITEM_DEFINE_RD in required_items:
        steps.append("Define `R_d` with the monic exact-degree component on the source-stated slot, not by symmetry.")
    if REQUIRED_ITEM_FINITENESS_CARDINALITY in required_items:
        steps.append("Add theorem statements for finiteness/cardinality in the same edit; `by sorry` is acceptable in autoformalize.")
    steps.append(
        "If a local API detail still blocks compilation after that edit, write `task_results/<file>.md` immediately instead of ending the session with no durable artifact."
    )
    return steps


def _autoformalization_starter_skeleton(contract: Mapping[str, Any]) -> str | None:
    required_items = set(_normalize_required_items(contract.get("requiredItems")))
    if not required_items:
        return None

    lines = [
        "-- Sketch only; adapt assumptions and names as needed, but keep the source object faithful.",
        "abbrev PolyDegreeLT (F : Type*) [Semiring F] (d : Nat) : Type _ :=",
        "  ↥(Polynomial.degreeLT F d)",
        "",
    ]
    if REQUIRED_ITEM_MONIC in required_items or REQUIRED_ITEM_EXACT_DEGREE in required_items:
        lines.extend(
            [
                "def MonicDegreeEq (F : Type*) [Semiring F] (d : Nat) : Type _ :=",
                "  { p : F[X] // p.Monic ∧ p.natDegree = d }",
                "",
            ]
        )
    if REQUIRED_ITEM_D_ZERO in required_items:
        lines.extend(
            [
                "-- Encode the explicit source singleton `Q0 = {(1, 0, 0)}`.",
                "def Q0 (F : Type*) [Semiring F] : Type _ :=",
                "  -- choose an explicit singleton encoding rather than erasing the special case",
                "  Unit",
                "",
            ]
        )
    if REQUIRED_ITEM_DEFINE_QD in required_items:
        qd_head = "def Qd (F : Type*) [Semiring F] (d : Nat) : Type _ :="
        if REQUIRED_ITEM_MONIC in required_items or REQUIRED_ITEM_EXACT_DEGREE in required_items:
            qd_body = "  MonicDegreeEq F d × PolyDegreeLT F d × PolyDegreeLT F d"
        else:
            qd_body = "  -- keep the distinguished source component faithful here"
        lines.extend([qd_head, qd_body, ""])
    if REQUIRED_ITEM_DEFINE_RD in required_items:
        rd_head = "def Rd (F : Type*) [Semiring F] (d : Nat) : Type _ :="
        if REQUIRED_ITEM_MONIC in required_items or REQUIRED_ITEM_EXACT_DEGREE in required_items:
            rd_body = "  PolyDegreeLT F d × PolyDegreeLT F d × MonicDegreeEq F d"
        else:
            rd_body = "  -- keep the distinguished source component faithful here"
        lines.extend([rd_head, rd_body, ""])
    if REQUIRED_ITEM_FINITENESS_CARDINALITY in required_items:
        lines.extend(
            [
                "theorem finite_Qd_or_Q0 ... := by",
                "  sorry",
                "",
                "theorem card_Qd ... := by",
                "  sorry",
                "",
            ]
        )
        if REQUIRED_ITEM_DEFINE_RD in required_items:
            lines.extend(
                [
                    "theorem finite_Rd ... := by",
                    "  sorry",
                    "",
                    "theorem card_Rd ... := by",
                    "  sorry",
                    "",
                ]
            )
    return "\n".join(lines).rstrip()


def _render_autoformalization_route_note(rel_path: str, contract: Mapping[str, Any]) -> str:
    lines = [
        f"# Autoformalization Route For `{rel_path}`",
        "",
        "Use this note together with the live formalization contract.",
        "",
    ]
    informal_objective = contract.get("informalObjective")
    if isinstance(informal_objective, str) and informal_objective.strip():
        lines.extend(
            [
                "## Informal Objective",
                "",
                informal_objective.strip(),
                "",
            ]
        )

    source_bundle_paths = _normalize_string_list(contract.get("sourceBundlePaths"))
    if source_bundle_paths:
        lines.extend(["## Source Bundle", ""])
        for path in source_bundle_paths:
            lines.append(f"- `{path}`")
        lines.append("")

    source_evidence = _normalize_string_list(contract.get("sourceEvidence"))
    if source_evidence:
        lines.extend(["## Source Evidence", ""])
        for item in source_evidence:
            lines.append(f"- {item}")
        lines.append("")

    required_items = _normalize_required_items(contract.get("requiredItems"))
    if required_items:
        required_details = contract.get("requiredItemDetails")
        lines.extend(["## Required Items", ""])
        for item in required_items:
            detail = _detail_text(required_details, item) or DEFAULT_REQUIRED_ITEM_DETAILS.get(item)
            if detail:
                lines.append(f"- `{item}`: {detail}")
            else:
                lines.append(f"- `{item}`")
        lines.append("")

    allowed_deferrals = _normalize_string_list(contract.get("allowedDeferrals"))
    if allowed_deferrals:
        lines.extend(["## Allowed Deferrals", ""])
        for item in allowed_deferrals:
            lines.append(f"- {item}")
        lines.append("")

    forbidden = _normalize_string_list(contract.get("forbiddenSimplifications"))
    if forbidden:
        forbidden_details = contract.get("forbiddenSimplificationDetails")
        lines.extend(["## Forbidden Simplifications", ""])
        for item in forbidden:
            detail = _detail_text(forbidden_details, item) or DEFAULT_FORBIDDEN_SIMPLIFICATION_DETAILS.get(item)
            if detail:
                lines.append(f"- `{item}`: {detail}")
            else:
                lines.append(f"- `{item}`")
        lines.append("")

    helper_notes = _normalize_string_list(contract.get("helperNotes"))
    if helper_notes:
        lines.extend(["## Helper Notes", ""])
        for item in helper_notes:
            lines.append(f"- `{item}`")
        lines.append("")

    first_edit_steps = _autoformalization_first_edit_steps(contract)
    if first_edit_steps:
        lines.extend(["## First Lean Edit", ""])
        for item in first_edit_steps:
            lines.append(f"- {item}")
        lines.append("")

    starter_skeleton = _autoformalization_starter_skeleton(contract)
    if starter_skeleton:
        lines.extend(
            [
                "## Starter Skeleton",
                "",
                "```lean",
                starter_skeleton,
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Acceptance Rule",
            "",
            "- Keep the source object faithful.",
            "- If the exact declaration layer is still blocked, leave the source file unchanged and write a durable blocker/task result instead of weakening the object.",
            "",
        ]
    )
    return "\n".join(lines)


def materialize_autoformalization_route_note(
    workspace: Path,
    rel_path: str,
    contract: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> str | None:
    if str(contract.get("sourceKind") or "theorem_header") != "comment_only":
        return None
    route_path = autoformalization_route_note_path(workspace, rel_path)
    if route_path.exists() and not overwrite:
        return route_path.relative_to(workspace).as_posix()
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text(_render_autoformalization_route_note(rel_path, contract), encoding="utf-8")
    return route_path.relative_to(workspace).as_posix()


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
    if FINITE_CARDINALITY_SOURCE_RE.search(bundle_text):
        items.append(REQUIRED_ITEM_FINITENESS_CARDINALITY)
    return items


def _derive_contract_from_source(workspace: Path, rel_path: str) -> dict[str, Any]:
    source_root = _workspace_source_root(workspace)
    source_path = source_root / rel_path
    source_text = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    source_kind = _source_kind(source_text)
    if source_kind != "comment_only":
        return {
            "sourceKind": source_kind,
            "sourceBundlePaths": [],
            "informalObjective": None,
            "requiredItems": [],
            "allowedDeferrals": [],
            "forbiddenSimplifications": [],
            "helperNotes": [],
        }

    bundle_paths = _bundle_paths(source_root, rel_path)
    bundle_text = "\n\n".join(path.read_text(encoding="utf-8") for path in bundle_paths)
    return _normalize_contract_payload(
        {
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
            "helperNotes": [],
        }
    )


def materialize_formalization_contract(
    workspace: Path,
    rel_path: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any] | None:
    contract_path = formalization_contract_path(workspace, rel_path)
    existing = _read_json(contract_path)
    if existing is not None and not overwrite:
        payload = _normalize_contract_payload(existing)
        payload["present"] = True
        payload["path"] = contract_path.relative_to(workspace).as_posix()
        payload["routeNotePath"] = materialize_autoformalization_route_note(workspace, rel_path, payload)
        return payload

    payload = _derive_contract_from_source(workspace, rel_path)
    if str(payload.get("sourceKind") or "theorem_header") != "comment_only":
        return None

    stored_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"present", "path"}
    }
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(stored_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stored_payload = _normalize_contract_payload(stored_payload)
    stored_payload["present"] = True
    stored_payload["path"] = contract_path.relative_to(workspace).as_posix()
    stored_payload["routeNotePath"] = materialize_autoformalization_route_note(
        workspace,
        rel_path,
        stored_payload,
        overwrite=overwrite,
    )
    return stored_payload


def materialize_formalization_contracts(workspace: Path, rel_paths: list[str], *, overwrite: bool = False) -> list[str]:
    written: list[str] = []
    for rel_path in rel_paths:
        payload = materialize_formalization_contract(workspace, rel_path, overwrite=overwrite)
        if payload is None:
            continue
        path = payload.get("path")
        if isinstance(path, str) and path:
            written.append(path)
    return written


def _load_or_derive_contract(workspace: Path, rel_path: str) -> dict[str, Any]:
    contract_path = formalization_contract_path(workspace, rel_path)
    payload = _read_json(contract_path)
    if payload is not None:
        payload = _normalize_contract_payload(payload)
        payload["present"] = True
        payload["path"] = contract_path.relative_to(workspace).as_posix()
        payload["routeNotePath"] = autoformalization_route_note_path(workspace, rel_path).relative_to(workspace).as_posix()
        return payload

    payload = _derive_contract_from_source(workspace, rel_path)
    source_kind = str(payload.get("sourceKind") or "theorem_header")
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
            "helperNotes": [],
        }

    payload["present"] = False
    payload["path"] = None
    payload["routeNotePath"] = autoformalization_route_note_path(workspace, rel_path).relative_to(workspace).as_posix()
    return payload


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
    if item == REQUIRED_ITEM_FINITENESS_CARDINALITY:
        return FINITE_CARDINALITY_DECL_RE.search(workspace_text) is not None
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
            "routeNotePath": contract.get("routeNotePath"),
        },
    }
