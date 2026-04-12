from __future__ import annotations

import runpy
from pathlib import Path


def main() -> int:
    script_path = Path(__file__).with_name("validate-review.py")
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:  # pragma: no cover - delegated legacy script exit
        code = exc.code
        return int(code) if isinstance(code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
