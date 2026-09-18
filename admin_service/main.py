"""Entrypoint for the standalone admin/dashboard service — /zero, /spice
(per-restaurant, owner-facing) and /admin (super-admin, sees both). No
pipecat/telephony dependency; separate deployment from app/main.py (the bot).
"""

from __future__ import annotations

import sys

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from loguru import logger

from admin_service.auth import scoped_basic_auth
from admin_service.config import RestaurantConfig, load_config, resolve_credentials
from admin_service.dashboard import render_restaurant_page, render_super_admin_page
from admin_service.sheets_reader import fetch_calls
from admin_service.stats import compute_stats

app = FastAPI(title="Restaurant Admin Dashboard")

try:
    config = load_config()
except Exception:
    logger.exception("admin_service: failed to load config.yaml — refusing to start")
    raise


def _load_restaurant_page(cfg: RestaurantConfig) -> str:
    calls = fetch_calls(cfg.google_sheet_id)
    stats = compute_stats(calls, cfg.minutes_allowed_per_month)
    return render_restaurant_page(cfg, calls, stats)


def _register_restaurant_route(cfg: RestaurantConfig) -> None:
    """One route per restaurant, each with its own auth dependency bound to
    that restaurant's own credentials (see auth.py). `cfg` is this
    function's own parameter, not the loop variable, so each route's
    closure correctly captures its own restaurant — no late-binding here."""
    auth_dep = Depends(scoped_basic_auth(resolve_credentials(cfg)))

    @app.get(cfg.admin_path, dependencies=[auth_dep], name=f"dashboard_{cfg.id}")
    async def _dashboard() -> HTMLResponse:
        return HTMLResponse(_load_restaurant_page(cfg))


for _cfg in config.restaurants.values():
    _register_restaurant_route(_cfg)


@app.get("/admin", dependencies=[Depends(scoped_basic_auth(resolve_credentials(config.super_admin)))])
async def super_admin_dashboard() -> HTMLResponse:
    entries = []
    for cfg in config.restaurants.values():
        calls = fetch_calls(cfg.google_sheet_id)
        entries.append((cfg, compute_stats(calls, cfg.minutes_allowed_per_month)))
    return HTMLResponse(render_super_admin_page(entries))


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {
        "status": "ok",
        "restaurants": [
            {"id": r.id, "display_name": r.display_name, "admin_path": r.admin_path}
            for r in config.restaurants.values()
        ],
    }


if __name__ == "__main__":
    import uvicorn

    logger.remove()
    logger.add(sys.stderr, level="INFO")
    uvicorn.run(app, host="0.0.0.0", port=8081)
