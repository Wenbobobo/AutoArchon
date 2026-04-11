from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


DECL_RE = re.compile(r"^(theorem|lemma)\s+([A-Za-z0-9_'.]+)\b")
PROCESS_PATTERNS = (
    re.compile(r"archon-loop\.sh"),
    re.compile(r"codex exec --json"),
)


@dataclass(frozen=True)
class HeaderDrift:
    rel_path: str
    declaration_name: str
    mutation_class: str
    source_header: str
    workspace_header: str

    def to_event(self) -> dict[str, str]:
        payload = asdict(self)
        payload["event"] = "header_mutation"
        return payload


def parse_allowed_files(scope_markdown: str) -> list[str]:
    allowed: list[str] = []
    capture = False
    for raw_line in scope_markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## Allowed Files"):
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        matches = re.findall(r"`([^`]+\.lean)`", line)
        allowed.extend(matches)
    return allowed


def read_allowed_files(workspace_root: Path) -> list[str]:
    run_scope = workspace_root / ".archon" / "RUN_SCOPE.md"
    if not run_scope.exists():
        return []
    return parse_allowed_files(run_scope.read_text(encoding="utf-8"))


def _normalize_space(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def _extract_headers(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        match = DECL_RE.match(line.strip())
        if not match:
            index += 1
            continue

        chunk = [line.strip()]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if next_line:
                chunk.append(next_line)
            if ":=" in next_line:
                break
            index += 1
        header = _normalize_space(" ".join(chunk))
        headers[match.group(2)] = header
        index += 1
    return headers


def _parse_header(header: str) -> tuple[str, list[str], str] | None:
    normalized = _normalize_space(header)
    if ":=" in normalized:
        normalized = normalized.split(":=", 1)[0].strip()
    match = re.match(r"^(theorem|lemma)\s+([A-Za-z0-9_'.]+)\s*(.*)$", normalized)
    if not match:
        return None

    name = match.group(2)
    remainder = match.group(3)

    depth = 0
    split_at: int | None = None
    for index, char in enumerate(remainder):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif char == ":" and depth == 0:
            split_at = index
            break
    if split_at is None:
        return None

    binders_part = remainder[:split_at].strip()
    conclusion = remainder[split_at + 1 :].strip()

    binders: list[str] = []
    index = 0
    while index < len(binders_part):
        char = binders_part[index]
        if char not in "([{":
            index += 1
            continue
        closing = { "(": ")", "[": "]", "{": "}" }[char]
        depth = 1
        start = index
        index += 1
        while index < len(binders_part) and depth > 0:
            current = binders_part[index]
            if current == char:
                depth += 1
            elif current == closing:
                depth -= 1
            index += 1
        binders.append(_normalize_space(binders_part[start:index]))
    return name, binders, conclusion


def _is_subsequence(source_items: list[str], target_items: list[str]) -> bool:
    if len(source_items) > len(target_items):
        return False
    current = 0
    for item in target_items:
        if current < len(source_items) and source_items[current] == item:
            current += 1
    return current == len(source_items)


def classify_header_mutation(source_header: str, workspace_header: str) -> str:
    if _normalize_space(source_header) == _normalize_space(workspace_header):
        return "none"

    source_parts = _parse_header(source_header)
    workspace_parts = _parse_header(workspace_header)
    if source_parts is None or workspace_parts is None:
        return "header_drift"
    if source_parts == workspace_parts:
        return "none"

    source_name, source_binders, source_conclusion = source_parts
    workspace_name, workspace_binders, workspace_conclusion = workspace_parts

    if source_name != workspace_name:
        return "renamed_declaration"
    if source_conclusion != workspace_conclusion:
        return "changed_conclusion"
    if len(workspace_binders) > len(source_binders) and _is_subsequence(source_binders, workspace_binders):
        return "added_hypothesis"
    return "header_drift"


def collect_header_drifts(
    source_root: Path,
    workspace_root: Path,
    *,
    allowed_files: list[str] | None = None,
) -> list[HeaderDrift]:
    if allowed_files:
        rel_paths = sorted(set(allowed_files))
    else:
        rel_paths = sorted(path.relative_to(source_root).as_posix() for path in source_root.rglob("*.lean"))

    drifts: list[HeaderDrift] = []
    for rel_path in rel_paths:
        source_path = source_root / rel_path
        workspace_path = workspace_root / rel_path
        if not source_path.exists() or not workspace_path.exists():
            continue
        source_headers = _extract_headers(source_path.read_text(encoding="utf-8"))
        workspace_headers = _extract_headers(workspace_path.read_text(encoding="utf-8"))
        for name, source_header in source_headers.items():
            workspace_header = workspace_headers.get(name)
            if workspace_header is None:
                continue
            mutation_class = classify_header_mutation(source_header, workspace_header)
            if mutation_class == "none":
                continue
            drifts.append(
                HeaderDrift(
                    rel_path=rel_path,
                    declaration_name=name,
                    mutation_class=mutation_class,
                    source_header=source_header,
                    workspace_header=workspace_header,
                )
            )
    return drifts


def collect_changed_files(source_root: Path, workspace_root: Path, *, allowed_files: list[str] | None = None) -> list[str]:
    if allowed_files:
        rel_paths = sorted(set(allowed_files))
    else:
        rel_paths = sorted(path.relative_to(source_root).as_posix() for path in source_root.rglob("*.lean"))
    changed: list[str] = []
    for rel_path in rel_paths:
        source_path = source_root / rel_path
        workspace_path = workspace_root / rel_path
        if not workspace_path.exists():
            continue
        source_text = source_path.read_text(encoding="utf-8") if source_path.exists() else None
        workspace_text = workspace_path.read_text(encoding="utf-8")
        if source_text != workspace_text:
            changed.append(rel_path)
    return changed


def list_runtime_process_lines(ps_output: str) -> list[str]:
    lines: list[str] = []
    for raw_line in ps_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "grep" in line or "ps -ef" in line:
            continue
        if any(pattern.search(line) for pattern in PROCESS_PATTERNS):
            lines.append(line)
    return lines


def latest_iteration_meta(workspace_root: Path) -> tuple[str | None, dict[str, object] | None]:
    log_root = workspace_root / ".archon" / "logs"
    meta_paths = sorted(log_root.glob("iter-*/meta.json"))
    if not meta_paths:
        return None, None
    latest = meta_paths[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return latest.parent.name, None
    return latest.parent.name, payload


def collect_meta_prover_errors(meta: dict[str, object] | None) -> list[str]:
    if not isinstance(meta, dict):
        return []
    provers = meta.get("provers")
    if not isinstance(provers, dict):
        return []

    failures: list[str] = []
    for prover_slug, payload in sorted(provers.items()):
        if not isinstance(payload, dict):
            continue
        if payload.get("status") != "error":
            continue
        rel_path = payload.get("file")
        if isinstance(rel_path, str) and rel_path:
            failures.append(rel_path)
        else:
            failures.append(str(prover_slug))
    return failures


def dumps_jsonl(events: list[dict[str, object]]) -> str:
    return "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
