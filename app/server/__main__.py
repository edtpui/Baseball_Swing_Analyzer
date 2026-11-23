"""Entry point: `python -m server` from the app folder."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from .app import ServerOptions, create_app

APP_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m server", description="Swing Analyzer laptop server")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=APP_DIR / "data", help="where swings and settings are kept")
    parser.add_argument("--simulate", action="store_true", help="use a simulated sensor instead of Bluetooth")
    parser.add_argument(
        "--sim-interval", type=float, default=None, metavar="SECONDS", help="simulated swing every N seconds"
    )
    parser.add_argument("--sim-seed", type=int, default=None, help="make simulated swings repeatable")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    options = ServerOptions(
        data_dir=args.data_dir,
        web_dir=APP_DIR / "web" / "dist",
        simulate=args.simulate,
        sim_interval_s=args.sim_interval,
        sim_seed=args.sim_seed,
    )
    uvicorn.run(create_app(options), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
