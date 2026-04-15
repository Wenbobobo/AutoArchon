#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from archonlib.campaign import DEFAULT_HEARTBEAT_SECONDS, build_campaign_overview, write_campaign_progress_surface


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve a lightweight browser view over AutoArchon campaign progress surfaces."
    )
    parser.add_argument("--campaign-root", required=True, help="Campaign root to observe")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Interface to bind the observer server to (use 0.0.0.0 for remote viewing)",
    )
    parser.add_argument("--port", type=int, default=8765, help="TCP port for the observer server")
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Heartbeat window used when refreshing campaign status",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=int,
        default=30,
        help="Minimum seconds between overview refreshes while serving requests",
    )
    parser.add_argument(
        "--no-refresh-status",
        action="store_true",
        help="Reuse campaign-status.json when present instead of forcing a fresh status recompute on each refresh",
    )
    return parser.parse_args()


def refresh_campaign_progress(
    campaign_root: Path,
    *,
    heartbeat_seconds: int,
    refresh_status: bool,
) -> dict:
    overview = build_campaign_overview(
        campaign_root,
        heartbeat_seconds=heartbeat_seconds,
        refresh_status=refresh_status,
    )
    write_campaign_progress_surface(campaign_root, overview)
    return overview


class CampaignObserverServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[SimpleHTTPRequestHandler],
        *,
        campaign_root: Path,
        heartbeat_seconds: int,
        refresh_seconds: int,
        refresh_status: bool,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.campaign_root = campaign_root.resolve()
        self.heartbeat_seconds = heartbeat_seconds
        self.refresh_seconds = max(0, int(refresh_seconds))
        self.refresh_status = refresh_status
        self._refresh_lock = threading.Lock()
        self._next_refresh_monotonic = 0.0
        self.last_refresh_error: str | None = None

    def refresh_if_due(self, *, force: bool = False) -> dict | None:
        now = time.monotonic()
        if not force and now < self._next_refresh_monotonic:
            return None
        with self._refresh_lock:
            now = time.monotonic()
            if not force and now < self._next_refresh_monotonic:
                return None
            overview = refresh_campaign_progress(
                self.campaign_root,
                heartbeat_seconds=self.heartbeat_seconds,
                refresh_status=self.refresh_status,
            )
            self._next_refresh_monotonic = now + self.refresh_seconds
            self.last_refresh_error = None
            return overview


def make_campaign_observer_handler(campaign_root: Path) -> type[SimpleHTTPRequestHandler]:
    root = campaign_root.resolve()

    class CampaignObserverHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def _refresh_or_500(self) -> bool:
            server = self.server
            assert isinstance(server, CampaignObserverServer)
            try:
                server.refresh_if_due()
            except Exception as exc:  # pragma: no cover - defensive branch
                server.last_refresh_error = str(exc)
                body = json.dumps({"error": "campaign_overview_refresh_failed", "detail": str(exc)}, indent=2) + "\n"
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body.encode("utf-8"))))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body.encode("utf-8"))
                return False
            return True

        def _maybe_redirect_root(self) -> bool:
            if self.path not in {"", "/"}:
                return False
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/control/progress-summary.html")
            self.end_headers()
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._refresh_or_500():
                return
            if self._maybe_redirect_root():
                return
            super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            if not self._refresh_or_500():
                return
            if self._maybe_redirect_root():
                return
            super().do_HEAD()

    return CampaignObserverHandler


def main() -> int:
    args = parse_args()
    campaign_root = Path(args.campaign_root).resolve()
    handler_class = make_campaign_observer_handler(campaign_root)
    server = CampaignObserverServer(
        (args.bind, args.port),
        handler_class,
        campaign_root=campaign_root,
        heartbeat_seconds=args.heartbeat_seconds,
        refresh_seconds=args.refresh_seconds,
        refresh_status=not args.no_refresh_status,
    )
    server.refresh_if_due(force=True)
    display_host = "127.0.0.1" if args.bind == "0.0.0.0" else args.bind
    try:
        print(
            f"Serving AutoArchon campaign observer for {campaign_root} at "
            f"http://{display_host}:{server.server_port}/control/progress-summary.html",
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
