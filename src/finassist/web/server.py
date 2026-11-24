"""Starts the GUI on 127.0.0.1 and opens it in the default browser."""

from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

from finassist.config.password import FILE_NAME as PASSWORD_FILE
from finassist.config.paths import Paths
from finassist.db.keys import KeyStore
from finassist.web.security import Guard
from finassist.web.state import WebConfig

__all__ = ["free_port", "serve"]

HOST = "127.0.0.1"


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
            except OSError:
                continue
            chosen: int = s.getsockname()[1]
            return chosen
    raise RuntimeError("no free local port")


def serve(
    main_paths: Paths,
    config_path: Path,
    *,
    real_keys: KeyStore,
    start_ledger: str = "demo",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    import uvicorn

    from finassist.config.system import computer_name
    from finassist.web.app import create_app

    port = free_port(port)
    guard = Guard({f"{HOST}:{port}", f"localhost:{port}"}, main_paths.home / PASSWORD_FILE)
    cfg = WebConfig(
        main_paths=main_paths, real_keys=real_keys, config_path=config_path, start_ledger=start_ledger
    )
    app = create_app(cfg, guard)
    url = f"http://{HOST}:{port}{guard.launch_path()}"
    print(f"Coinleaf is running on {computer_name()} only.", flush=True)
    print(f"Open: {url}", flush=True)
    print("Keep this window open while you use it. Press Ctrl+C here to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False, server_header=False)
