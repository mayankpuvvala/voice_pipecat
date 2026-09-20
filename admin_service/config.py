"""Loads config.yaml: the restaurant registry for this standalone admin
service. Hard separation is structural — main.py wires each
RestaurantConfig directly to its own route, never a shared secrets dict.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass(frozen=True)
class RestaurantConfig:
    id: str
    display_name: str
    admin_path: str
    google_sheet_id: str
    admin_username_env: str
    admin_password_env: str
    minutes_allowed_per_month: int


@dataclass(frozen=True)
class SuperAdminConfig:
    admin_username_env: str
    admin_password_env: str


@dataclass(frozen=True)
class AppConfig:
    restaurants: dict[str, RestaurantConfig]  # keyed by restaurant id
    super_admin: SuperAdminConfig


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def _require(d: dict, key: str, context: str) -> object:
    value = d.get(key)
    if value in (None, ""):
        raise ValueError(f"config.yaml: '{key}' is required and non-empty ({context})")
    return value


def _load_restaurant(restaurant_id: str, raw: dict) -> RestaurantConfig:
    context = f"restaurants.{restaurant_id}"
    return RestaurantConfig(
        id=restaurant_id,
        display_name=str(_require(raw, "display_name", context)),
        admin_path=str(_require(raw, "admin_path", context)),
        google_sheet_id=str(_require(raw, "google_sheet_id", context)),
        admin_username_env=str(_require(raw, "admin_username_env", context)),
        admin_password_env=str(_require(raw, "admin_password_env", context)),
        minutes_allowed_per_month=int(_require(raw, "minutes_allowed_per_month", context)),
    )


def load_config(path: Path = _CONFIG_PATH) -> AppConfig:
    """Parse + validate config.yaml; raises ValueError rather than serving
    a half-configured dashboard."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    raw_restaurants = raw.get("restaurants") or {}
    if not raw_restaurants:
        raise ValueError("config.yaml: 'restaurants' must list at least one restaurant")

    restaurants: dict[str, RestaurantConfig] = {}
    seen_paths: dict[str, str] = {}
    for restaurant_id, raw_cfg in raw_restaurants.items():
        cfg = _load_restaurant(restaurant_id, raw_cfg or {})
        if cfg.admin_path in seen_paths:
            raise ValueError(
                f"config.yaml: admin_path '{cfg.admin_path}' is used by both "
                f"'{seen_paths[cfg.admin_path]}' and '{restaurant_id}' — must be unique"
            )
        seen_paths[cfg.admin_path] = restaurant_id
        restaurants[restaurant_id] = cfg

    raw_super = raw.get("super_admin") or {}
    super_admin = SuperAdminConfig(
        admin_username_env=str(_require(raw_super, "admin_username_env", "super_admin")),
        admin_password_env=str(_require(raw_super, "admin_password_env", "super_admin")),
    )

    return AppConfig(restaurants=restaurants, super_admin=super_admin)


def resolve_credentials(cfg: RestaurantConfig | SuperAdminConfig) -> Credentials:
    """Read this one config's own two env vars. Missing -> empty string,
    same "gate is open until set" behavior as app/admin/auth.py — don't
    expose this on a public URL before setting real values."""
    return Credentials(
        username=os.environ.get(cfg.admin_username_env, ""),
        password=os.environ.get(cfg.admin_password_env, ""),
    )
