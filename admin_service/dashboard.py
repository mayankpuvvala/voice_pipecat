"""HTML rendering for the dashboard pages. Small composable pieces (one
job each) rather than one long page-builder — see app/admin/routes.py for
the CSS/table styling this borrows.
"""

from __future__ import annotations

import math
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from admin_service.config import AppConfig, RestaurantConfig
from admin_service.stats import categorize_topics

_IST = ZoneInfo("Asia/Kolkata")

_FONT_LINKS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Work+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
"""

_STYLE = """
:root {
  --paper: #f4f2ec;
  --on-dark: #f4f2ec;
  --surface: #ffffff;
  --surface-2: #faf9f5;
  --ink: #221f1a;
  --muted: #78716a;
  --faint: #a39c8f;
  --border: #e3dfd3;
  --border-strong: #cfc9b8;

  --accent: #7a2333;
  --accent-ink: #ffffff;
  --accent-soft: #f6e6e9;
  --accent-soft-border: #e9c3cb;

  --tint-amber: #fbeed9; --tint-amber-ink: #7a4a10;
  --tint-blue: #e4eef4; --tint-blue-ink: #285068;
  --tint-wine: #f6e6e9; --tint-wine-ink: #7a2333;
  --tint-green: #e5f0e6; --tint-green-ink: #2f5c3f;
  --tint-red: #f7e6e3; --tint-red-ink: #8a3524;

  --good: #2f7d5a;
  --warn: #b8791e;
  --critical: #b23a2e;

  --shadow: 0 1px 2px rgba(34,31,26,0.06), 0 8px 24px -12px rgba(34,31,26,0.12);
  --shadow-lift: 0 4px 8px rgba(34,31,26,0.08), 0 16px 40px -16px rgba(34,31,26,0.22);
  --radius: 14px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #1a1713; --surface: #221e19; --surface-2: #27221c;
    --ink: #f1ece2; --muted: #a89f90; --faint: #766e60;
    --border: #38332a; --border-strong: #4a4436;
    --accent: #d97d90; --accent-ink: #201014;
    --accent-soft: #33232a; --accent-soft-border: #4a2f38;
    --tint-amber: #332818; --tint-amber-ink: #e8b978;
    --tint-blue: #1c2b33; --tint-blue-ink: #8fc2dc;
    --tint-wine: #33232a; --tint-wine-ink: #e8a4b4;
    --tint-green: #1f2e22; --tint-green-ink: #8fce9f;
    --tint-red: #331f1a; --tint-red-ink: #e19181;
    --good: #63b98a; --warn: #e3aa53; --critical: #e08170;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -12px rgba(0,0,0,0.5);
    --shadow-lift: 0 4px 8px rgba(0,0,0,0.35), 0 16px 40px -16px rgba(0,0,0,0.6);
  }
}

* { box-sizing: border-box; }
html { color-scheme: light dark; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: "Work Sans", -apple-system, "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }
a { color: var(--accent); }
h1, h2, h3 { font-family: "Fraunces", Georgia, serif; font-weight: 600; text-wrap: balance; margin: 0; }
.muted { color: var(--muted); font-size: 0.78rem; }
.nowrap { white-space: nowrap; }

.topbar { background: var(--ink); color: var(--on-dark); }
@media (prefers-color-scheme: dark) { .topbar { background: #0f0d0b; } }
.topbar-inner { max-width: 1240px; margin: 0 auto; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.brand { display: flex; align-items: baseline; gap: 10px; }
.brand-mark { font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 1.28rem; }
.brand-sub { color: rgba(244,242,236,0.55); font-size: 0.78rem; }
.topbar-actions { display: flex; align-items: center; gap: 18px; font-size: 0.82rem; }
.topbar-actions a { color: rgba(244,242,236,0.82); text-decoration: none; border-bottom: 1px solid rgba(244,242,236,0.3); }
.topbar-actions a:hover { color: #fff; border-color: rgba(244,242,236,0.7); }

.shell { max-width: 1240px; margin: 0 auto; padding: 0 24px 64px; }
.page-head { padding: 28px 0 22px; }
.page-head h1 { font-size: 1.9rem; }
.page-head p { margin: 6px 0 0; color: var(--muted); font-size: 0.88rem; max-width: 60ch; }

section { margin-top: 26px; }
.card { border-radius: var(--radius); border: 1px solid var(--border); background: var(--surface); box-shadow: var(--shadow); padding: 20px 22px; }
.card h3 { font-size: 1rem; margin-bottom: 14px; }
.split { display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; align-items: stretch; }
.split.charts-split { grid-template-columns: 1.6fr 1fr; }

/* KPI row */
.kpi-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.kpi { border-radius: var(--radius); padding: 16px 18px; border: 1px solid var(--border); background: var(--surface-2); box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 8px; }
.kpi.tint-amber { background: var(--tint-amber); }
.kpi.tint-blue { background: var(--tint-blue); }
.kpi.tint-wine { background: var(--tint-wine); }
.kpi.tint-green { background: var(--tint-green); }
.kpi.tint-red { background: var(--tint-red); }
.kpi-clickable { cursor: pointer; }
.kpi-clickable:hover { box-shadow: var(--shadow-lift); }
.kpi-label { font-size: 0.74rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.72; }
.kpi-value { font-family: "Fraunces", Georgia, serif; font-size: 1.55rem; font-weight: 600; line-height: 1; }
.kpi-value small { font-family: "Work Sans", sans-serif; font-weight: 500; font-size: 0.95rem; opacity: 0.6; }
.kpi-note { font-size: 0.74rem; opacity: 0.75; }
.kpi-delta { font-size: 0.76rem; font-weight: 600; display: flex; align-items: center; gap: 4px; }
.kpi-delta.up { color: var(--good); }
.kpi-delta.down { color: var(--critical); }
.kpi-delta.flat { color: var(--muted); }
.kpi-delta .note { font-weight: 400; opacity: 0.65; }
.kpi-track { height: 6px; border-radius: 999px; background: rgba(0,0,0,0.08); overflow: hidden; }
.kpi-track-fill { height: 100%; border-radius: 999px; }

.mini-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 12px; }
.mini { border-radius: var(--radius); padding: 14px 18px; border: 1px solid var(--border); background: var(--surface); box-shadow: var(--shadow); display: flex; align-items: center; justify-content: space-between; }
.mini-label { font-size: 0.8rem; color: var(--muted); }
.mini-value { font-family: "Fraunces", Georgia, serif; font-size: 1.2rem; font-weight: 600; }

/* donut */
.donut-row { display: flex; align-items: center; gap: 26px; flex-wrap: wrap; }
.donut-legend { display: flex; flex-direction: column; gap: 12px; font-size: 0.88rem; }
.legend-item { display: flex; align-items: center; gap: 9px; }
.legend-swatch { width: 11px; height: 11px; border-radius: 3px; flex-shrink: 0; }
.legend-count { font-weight: 700; margin-left: auto; padding-left: 14px; font-family: "Fraunces", Georgia, serif; }

/* topics list */
.topic-list { display: flex; flex-direction: column; }
.topic-row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); cursor: pointer; border-radius: 8px; }
.topic-row:last-child { border-bottom: none; padding-bottom: 0; }
.topic-row:first-child { padding-top: 0; }
.topic-row:hover { background: var(--surface-2); }
.topic-row.topic-active { outline: 2px solid var(--accent); outline-offset: 2px; }
.topic-rank { width: 22px; height: 22px; border-radius: 7px; background: var(--surface-2); border: 1px solid var(--border); display: flex; align-items: center; justify-content: center; font-size: 0.72rem; font-weight: 700; color: var(--muted); flex-shrink: 0; }
.topic-name { font-weight: 600; font-size: 0.88rem; flex: 1; min-width: 0; }
.topic-bar-track { height: 5px; border-radius: 999px; background: var(--surface-2); width: 100%; margin-top: 4px; }
.topic-bar-fill { height: 100%; border-radius: 999px; background: var(--accent); }
.topic-count { font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 0.95rem; flex-shrink: 0; min-width: 2.4em; text-align: right; }

/* stick charts */
.stick-chart { display: flex; align-items: flex-end; gap: 4px; height: 148px; padding: 4px 2px 0; overflow-x: auto; }
.stick-col { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; flex: 1 0 18px; height: 100%; }
.stick-count { font-size: 0.62rem; color: var(--faint); height: 13px; }
.stick-bar { width: 7px; min-height: 4px; border-radius: 999px; background: var(--accent); }
.stick-label { font-size: 0.66rem; color: var(--muted); margin-top: 5px; }

/* table card */
.table-card { padding: 0; overflow: hidden; }
.table-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 18px 22px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.table-head h3 { margin: 0; }
.table-tools { display: flex; align-items: center; gap: 10px; }
.result-count { font-size: 0.8rem; color: var(--muted); }
.topic-chip { display: inline-flex; align-items: center; gap: 6px; background: var(--accent-soft); border: 1px solid var(--accent-soft-border); color: var(--accent); border-radius: 999px; padding: 3px 10px; font-size: 0.76rem; font-weight: 600; cursor: pointer; }
.topic-chip[hidden] { display: none; }

.btn { font-family: inherit; font-size: 0.84rem; font-weight: 600; cursor: pointer; border-radius: 9px; padding: 8px 14px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--ink); display: inline-flex; align-items: center; gap: 7px; }
.btn:hover { background: var(--surface-2); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.btn-primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.btn-primary:hover { filter: brightness(1.06); }
.btn-ghost { border-color: transparent; background: transparent; padding: 8px 6px; }
.btn-ghost:hover { background: var(--surface-2); }
.filter-btn { position: relative; }
.filter-count { background: var(--accent); color: var(--accent-ink); font-size: 0.68rem; font-weight: 700; min-width: 17px; height: 17px; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center; padding: 0 4px; }
.filter-count[hidden] { display: none; }

.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.84rem; min-width: 920px; }
thead th { text-align: left; font-weight: 600; color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; padding: 10px 22px; background: var(--surface-2); border-bottom: 1px solid var(--border); white-space: nowrap; }
tbody td { padding: 12px 22px; border-bottom: 1px solid var(--border); vertical-align: top; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: var(--surface-2); }
.caller-name { font-weight: 600; }
.caller-phone { color: var(--muted); font-size: 0.78rem; }

.badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px; font-size: 0.74rem; font-weight: 600; white-space: nowrap; margin: 1px 4px 1px 0; }
.badge-topic { background: var(--tint-blue); color: var(--tint-blue-ink); }
.badge-good { background: var(--tint-green); color: var(--tint-green-ink); }
.badge-warn { background: var(--tint-amber); color: var(--tint-amber-ink); }
.badge-critical { background: var(--tint-red); color: var(--tint-red-ink); }
.badge-neutral { background: var(--surface-2); color: var(--muted); border: 1px solid var(--border); }
.dot { width: 6px; height: 6px; border-radius: 999px; background: currentColor; }
.listen-link { color: var(--accent); font-weight: 600; text-decoration: none; font-size: 0.82rem; }
.listen-link:hover { text-decoration: underline; }
.view-transcript { background: none; border: none; font: inherit; padding: 0; cursor: pointer; }

.pagination { display: flex; align-items: center; justify-content: center; gap: 16px; padding: 16px 22px; }
.pagination span { font-size: 0.8rem; color: var(--muted); }
.pagination .btn:disabled { opacity: 0.4; cursor: default; }

.empty-note { text-align: center; color: var(--muted); font-size: 0.85rem; padding: 40px 20px; }

/* filter drawer */
.scrim { position: fixed; inset: 0; background: rgba(20,17,13,0.4); backdrop-filter: blur(1px); opacity: 0; pointer-events: none; transition: opacity 0.18s ease; z-index: 40; }
.scrim.open { opacity: 1; pointer-events: auto; }
@media (prefers-color-scheme: dark) { .scrim { background: rgba(0,0,0,0.6); } }

.drawer { position: fixed; top: 0; right: 0; height: 100%; width: min(380px, 92vw); background: var(--surface); border-left: 1px solid var(--border); box-shadow: var(--shadow-lift); transform: translateX(100%); transition: transform 0.22s ease; z-index: 41; display: flex; flex-direction: column; }
.drawer.open { transform: translateX(0); }
.drawer-head { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px; border-bottom: 1px solid var(--border); }
.drawer-head h3 { font-size: 1.05rem; }
.drawer-body { padding: 6px 20px 20px; overflow-y: auto; flex: 1; }
.drawer-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 16px 20px; border-top: 1px solid var(--border); }

.filter-block { padding: 18px 0; border-bottom: 1px solid var(--border); }
.filter-block:last-child { border-bottom: none; }
.filter-block-label { font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 10px; }
.preset-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.preset-pill { font-size: 0.78rem; padding: 6px 11px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--surface); cursor: pointer; }
.preset-pill.active { background: var(--accent-soft); border-color: var(--accent-soft-border); color: var(--accent); font-weight: 600; }
.date-row { display: flex; gap: 8px; }
.date-row label { flex: 1; font-size: 0.76rem; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.date-row input, select { font-family: inherit; font-size: 0.84rem; padding: 7px 9px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--ink); width: 100%; }
.check-grid { display: flex; flex-direction: column; gap: 9px; }
.check-row { display: flex; align-items: center; gap: 9px; font-size: 0.88rem; cursor: pointer; }
.check-row input { accent-color: var(--accent); width: 15px; height: 15px; }

/* transcript modal */
.modal-scrim { position: fixed; inset: 0; background: rgba(20,17,13,0.45); backdrop-filter: blur(1px); opacity: 0; pointer-events: none; transition: opacity 0.16s ease; z-index: 50; display: flex; align-items: center; justify-content: center; padding: 24px; }
.modal-scrim.open { opacity: 1; pointer-events: auto; }
@media (prefers-color-scheme: dark) { .modal-scrim { background: rgba(0,0,0,0.65); } }
.modal { width: min(560px, 100%); max-height: min(640px, 86vh); background: var(--surface); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow-lift); display: flex; flex-direction: column; transform: translateY(10px) scale(0.98); opacity: 0; transition: transform 0.16s ease, opacity 0.16s ease; }
.modal-scrim.open .modal { transform: translateY(0) scale(1); opacity: 1; }
.modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 18px 20px; border-bottom: 1px solid var(--border); }
.modal-head h3 { font-size: 1.05rem; margin-bottom: 3px; }
.modal-meta { font-size: 0.8rem; color: var(--muted); }
.modal-body { padding: 18px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.modal-foot { padding: 14px 20px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end; }
.turn { max-width: 82%; padding: 9px 13px; border-radius: 13px; font-size: 0.86rem; line-height: 1.45; }
.turn-label { font-size: 0.66rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 3px; opacity: 0.65; }
.turn-caller { align-self: flex-start; background: var(--surface-2); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
.turn-bot { align-self: flex-end; background: var(--accent-soft); border: 1px solid var(--accent-soft-border); color: var(--ink); border-bottom-right-radius: 4px; }

/* legacy pieces still used by the home + super-admin pages */
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 1.5rem; }
.tile { background: var(--surface); border-radius: 10px; box-shadow: var(--shadow); padding: 14px 18px; min-width: 140px; border: 1px solid var(--border); }
.tile .value { font-size: 1.5rem; font-weight: 700; }
.tile .label { color: var(--muted); font-size: 0.78rem; }
.tile-link { text-decoration: none; color: inherit; display: block; }
.tile-link:hover { box-shadow: var(--shadow-lift); }
.route-list { list-style: none; margin: 0 0 1.5rem; padding: 6px 20px; background: var(--surface); border-radius: 10px; box-shadow: var(--shadow); border: 1px solid var(--border); font-size: 0.85rem; }
.route-list li { padding: 8px 0; border-bottom: 1px solid var(--border); }
.route-list li:last-child { border-bottom: none; }
.restaurant-links a { display: inline-block; margin-right: 14px; font-weight: 600; }
.cap-bar-track { background: rgba(0,0,0,0.08); border-radius: 999px; height: 10px; width: 100%; max-width: 320px; overflow: hidden; }
.cap-bar-fill { height: 100%; border-radius: 999px; }
.trend { font-size: 0.75rem; font-weight: 700; margin-top: 6px; }
.trend-up { color: var(--good); }
.trend-down { color: var(--critical); }
.trend-flat { color: var(--muted); }
.trend-note { font-weight: 400; color: var(--muted); }
"""

_CONFIDENCE_BADGES = {
    0: ("—", "badge-neutral"),
    1: ("High", "badge-good"),
    2: ("Medium", "badge-warn"),
    3: ("Low", "badge-critical"),
}


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)}</title>
{_FONT_LINKS}<style>{_STYLE}</style></head><body>{body}</body></html>"""


def _format_datetime(iso_ts: str) -> tuple[str, str, str]:
    """Returns (date as dd/mm/yy, time as h:mm:ss am/pm, date as yyyy-mm-dd
    for filtering) in IST, or ("—", "—", ""). dd/mm/yy with slashes (not
    colons) and 12-hour time so the date doesn't read as a second clock
    time stacked under the real one."""
    if not iso_ts:
        return "—", "—", ""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "—", "—", ""
    local = dt.astimezone(_IST)
    date_str = local.strftime("%d/%m/%y")
    time_str = local.strftime("%I:%M:%S %p").lstrip("0").lower()
    return date_str, time_str, local.strftime("%Y-%m-%d")


def _format_duration(duration_secs: Any) -> str:
    """Returns "1min 42secs" style, or "—" if unknown/unparseable."""
    if duration_secs in (None, ""):
        return "—"
    try:
        total = int(round(float(duration_secs)))
    except (TypeError, ValueError):
        return "—"
    minutes, seconds = divmod(max(total, 0), 60)
    return f"{minutes}min {seconds}secs"


def _duration_seconds(duration_secs: Any) -> float:
    """Numeric seconds for sorting, or -1 for unknown/unparseable (sorts last
    when sorting longest-first)."""
    if duration_secs in (None, ""):
        return -1.0
    try:
        return float(duration_secs)
    except (TypeError, ValueError):
        return -1.0


def _cap_color(pct: float) -> str:
    if pct >= 100:
        return "var(--critical)"
    if pct >= 80:
        return "var(--warn)"
    return "var(--good)"


def render_trend(pct_change: float) -> str:
    """Month-over-month trend badge. Literal magnitude, not metric-specific
    "good/bad" semantics: red for a decrease, green for an increase, gray
    for no change or no previous-month baseline to compare against yet."""
    if pct_change > 0:
        css_class, arrow = "trend-up", "▲"
    elif pct_change < 0:
        css_class, arrow = "trend-down", "▼"
    else:
        css_class, arrow = "trend-flat", "＝"
    sign = "+" if pct_change >= 0 else ""
    return (
        f'<div class="trend {css_class}">{arrow} {sign}{pct_change:.1f}% '
        f'<span class="trend-note">vs last month</span></div>'
    )


def render_cap_bar(
    minutes_used: float, minutes_allowed: int, minutes_remaining: float, pct: float, pct_change: float
) -> str:
    fill_pct = min(pct, 100)
    color = _cap_color(pct)
    return f"""<div>
  <div class="cap-bar-track"><div class="cap-bar-fill" style="width:{fill_pct:.0f}%;background:{color}"></div></div>
  <span class="muted">{minutes_used:.0f} / {minutes_allowed} min used this month ({pct:.0f}%) &middot; {minutes_remaining:.0f} min remaining</span>
  {render_trend(pct_change)}
</div>"""


def _kpi_delta(pct_change: float) -> str:
    if pct_change > 0:
        css_class, arrow = "up", "▲"
    elif pct_change < 0:
        css_class, arrow = "down", "▼"
    else:
        css_class, arrow = "flat", "＝"
    sign = "+" if pct_change >= 0 else ""
    return (
        f'<span class="kpi-delta {css_class}">{arrow} {sign}{pct_change:.1f}% '
        f'<span class="note">vs last month</span></span>'
    )


def render_kpi_row(stats: dict[str, Any]) -> str:
    minutes_pct = stats["minutes_used_pct"]
    minutes_tile = f"""<div class="kpi tint-amber">
    <span class="kpi-label">Minutes used</span>
    <span class="kpi-value">{stats['minutes_used_this_month']:.0f} <small>/ {stats['minutes_allowed_per_month']:,}</small></span>
    <div class="kpi-track"><div class="kpi-track-fill" style="width:{min(minutes_pct, 100):.0f}%;background:{_cap_color(minutes_pct)}"></div></div>
    <span class="kpi-note">{minutes_pct:.0f}% used &middot; {stats['minutes_remaining_this_month']:.0f} min remaining</span>
    {_kpi_delta(stats['minutes_pct_change'])}</div>"""
    week_tile = f"""<div class="kpi tint-blue">
    <span class="kpi-label">Calls this week</span>
    <span class="kpi-value">{stats['total_calls_this_week']}</span></div>"""
    month_tile = f"""<div class="kpi tint-wine">
    <span class="kpi-label">Calls this month</span>
    <span class="kpi-value">{stats['total_calls_this_month']:,}</span>
    {_kpi_delta(stats['calls_pct_change'])}</div>"""
    all_time_tile = f"""<div class="kpi tint-green">
    <span class="kpi-label">Calls all-time</span>
    <span class="kpi-value">{stats['total_calls_all_time']:,}</span></div>"""
    followups_tile = f"""<div class="kpi tint-red kpi-clickable" id="tile-followups"
    data-from="{stats['this_month_start_iso']}" data-to="{stats['today_iso']}"
    title="Click to filter the call log below to this month's follow-ups">
    <span class="kpi-label">Follow-ups needed</span>
    <span class="kpi-value">{stats['followups_needed_this_month']:,}</span>
    {_kpi_delta(stats['followups_pct_change'])}</div>"""
    mini = "".join(
        f'<div class="mini"><span class="mini-label">{escape(k)}</span><span class="mini-value">{v}</span></div>'
        for k, v in (
            ("Avg call length", f"{stats['avg_call_minutes']:.1f} min"),
            ("Shortest / longest", f"{stats['min_call_minutes']:.1f} / {stats['max_call_minutes']:.1f} min"),
        )
    )
    return f"""<div class="kpi-row">{minutes_tile}{week_tile}{month_tile}{all_time_tile}{followups_tile}</div>
  <div class="mini-row">{mini}</div>"""


def render_outcome_donut(resolved: int, followup: int) -> str:
    """Call outcome this month, Resolved vs Follow-up needed, as a two-arc
    SVG ring (stroke-dasharray sized to each arc's own share of the
    circumference, not the full circle, so the split is proportional)."""
    total = resolved + followup
    if total == 0:
        return '<p class="muted">No calls logged yet this month.</p>'
    resolved_pct = resolved / total * 100
    r = 52
    circumference = 2 * math.pi * r
    resolved_len = circumference * resolved_pct / 100
    followup_len = circumference - resolved_len
    return f"""<div class="donut-row">
  <svg width="132" height="132" viewBox="0 0 132 132" role="img" aria-label="{resolved_pct:.0f} percent resolved">
    <circle cx="66" cy="66" r="{r}" fill="none" stroke="var(--surface-2)" stroke-width="16"/>
    <circle cx="66" cy="66" r="{r}" fill="none" stroke="var(--good)" stroke-width="16"
      stroke-dasharray="{resolved_len:.2f} {circumference:.2f}" stroke-dashoffset="0" stroke-linecap="round"
      transform="rotate(-90 66 66)"/>
    <circle cx="66" cy="66" r="{r}" fill="none" stroke="var(--critical)" stroke-width="16"
      stroke-dasharray="{followup_len:.2f} {circumference:.2f}" stroke-dashoffset="{-resolved_len:.2f}" stroke-linecap="round"
      transform="rotate(-90 66 66)"/>
    <text x="66" y="62" text-anchor="middle" font-family="Fraunces, Georgia, serif" font-weight="600" font-size="22" fill="var(--ink)">{resolved_pct:.0f}%</text>
    <text x="66" y="80" text-anchor="middle" font-family="Work Sans, sans-serif" font-size="10" fill="var(--muted)">RESOLVED</text>
  </svg>
  <div class="donut-legend">
    <div class="legend-item"><span class="legend-swatch" style="background:var(--good)"></span>Resolved<span class="legend-count">{resolved}</span></div>
    <div class="legend-item"><span class="legend-swatch" style="background:var(--critical)"></span>Follow-up needed<span class="legend-count">{followup}</span></div>
  </div>
</div>"""


def render_topic_breakdown(topic_counts: dict[str, int]) -> str:
    if not topic_counts:
        return '<p class="muted">No categorized topics yet.</p>'
    peak = max(topic_counts.values()) or 1
    rows = "".join(
        f'<div class="topic-row badge-clickable" data-topic="{escape(name)}" '
        f'title="Click to filter the call log below to this topic">'
        f'<span class="topic-rank">{i}</span>'
        f'<div style="flex:1;min-width:0"><span class="topic-name">{escape(name)}</span>'
        f'<div class="topic-bar-track"><div class="topic-bar-fill" style="width:{count / peak * 100:.0f}%"></div></div></div>'
        f'<span class="topic-count">{count}</span></div>'
        for i, (name, count) in enumerate(topic_counts.items(), start=1)
    )
    return f'<div class="topic-list" id="topic-list">{rows}</div>'


def render_hour_chart(hour_counts: list[int]) -> str:
    if not any(hour_counts):
        return '<p class="muted">No call-time data yet.</p>'
    peak = max(hour_counts) or 1
    cols = "".join(
        f'<div class="stick-col" title="{h:02d}:00 &middot; {count} call(s)">'
        f'<span class="stick-count">{count or ""}</span>'
        f'<div class="stick-bar" style="height:{(count / peak) * 100:.0f}%"></div>'
        f'<span class="stick-label">{h}</span>'
        f'</div>'
        for h, count in enumerate(hour_counts)
    )
    return f'<div class="stick-chart">{cols}</div>'


_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def render_day_chart(day_counts: list[int]) -> str:
    if not any(day_counts):
        return '<p class="muted">No call-time data yet.</p>'
    peak = max(day_counts) or 1
    cols = "".join(
        f'<div class="stick-col" title="{_DAY_LABELS[d]} &middot; {count} call(s)">'
        f'<span class="stick-count">{count or ""}</span>'
        f'<div class="stick-bar" style="height:{(count / peak) * 100:.0f}%"></div>'
        f'<span class="stick-label">{_DAY_LABELS[d]}</span>'
        f'</div>'
        for d, count in enumerate(day_counts)
    )
    return f'<div class="stick-chart">{cols}</div>'


def render_call_row(call: dict[str, Any]) -> str:
    date_str, time_str, iso_date = _format_datetime(call["timestamp"])
    duration_str = _format_duration(call.get("duration_secs"))
    outcome_key = "followup" if call["needs_followup"] else "resolved"
    confidence_key = call["confidence_rank"]
    categories = categorize_topics(call["topics"])
    topic_html = "".join(f'<span class="badge badge-topic">{escape(c)}</span>' for c in categories) or "—"

    outcome_badge = (
        '<span class="badge badge-critical"><span class="dot"></span>Follow-up</span>'
        if call["needs_followup"]
        else '<span class="badge badge-good"><span class="dot"></span>Resolved</span>'
    )

    if call["recording_url"]:
        recording_html = f'<a class="listen-link" href="{escape(call["recording_url"])}" target="_blank" rel="noopener">▶ Listen</a>'
    else:
        recording_html = '<span class="muted">—</span>'

    transcript = call.get("transcript") or ""
    if transcript.strip():
        caller_label = escape(call["caller_name"] or "—")
        when_label = escape(f"{date_str} {time_str}")
        phone_label = escape(call["caller_phone"] or "—")
        transcript_html = (
            '<button type="button" class="listen-link view-transcript" '
            f'data-caller="{caller_label}" data-when="{when_label}" '
            f'data-duration="{escape(duration_str)}" data-phone="{phone_label}" '
            f'data-transcript="{escape(transcript)}">View</button>'
        )
    else:
        transcript_html = '<span class="muted">—</span>'

    conf_label, conf_class = _CONFIDENCE_BADGES.get(call["confidence_rank"], _CONFIDENCE_BADGES[0])
    topics_attr = escape("|".join(categories))
    ts_attr = escape(call["timestamp"] or "")
    duration_attr = _duration_seconds(call.get("duration_secs"))
    escalation_key = "yes" if call["escalated"] else "no"

    escalation_badge = (
        '<span class="badge badge-critical"><span class="dot"></span>Escalated</span>'
        if call["escalated"]
        else '<span class="badge badge-neutral">—</span>'
    )

    return f"""<tr data-date="{iso_date}" data-ts="{ts_attr}" data-duration="{duration_attr}" data-outcome="{outcome_key}" data-confidence="{confidence_key}" data-escalation="{escalation_key}" data-topics="{topics_attr}">
    <td class="nowrap">{date_str} {time_str}</td>
    <td><div class="caller-name">{escape(call["caller_name"] or "—")}</div><div class="caller-phone">{escape(call["caller_phone"] or "—")}</div></td>
    <td>{topic_html}</td>
    <td>{outcome_badge}</td>
    <td>{recording_html}</td>
    <td>{transcript_html}</td>
    <td><span class="badge {conf_class}">{conf_label}</span></td>
    <td>{escalation_badge}</td>
  </tr>"""


_FILTER_DRAWER = """
<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" aria-hidden="true">
  <div class="drawer-head">
    <h3>Filter &amp; sort</h3>
    <button class="btn btn-ghost" id="drawer-close" aria-label="Close filters">✕</button>
  </div>
  <div class="drawer-body">
    <div class="filter-block">
      <div class="filter-block-label">Date range</div>
      <div class="preset-row" id="preset-row"
        data-week="{week_start}" data-month="{month_start}" data-30d="{last_30d_start}" data-today="{today}">
        <button type="button" class="preset-pill" data-preset="week">This week</button>
        <button type="button" class="preset-pill" data-preset="month">This month</button>
        <button type="button" class="preset-pill" data-preset="30d">Last 30 days</button>
        <button type="button" class="preset-pill active" data-preset="all">All time</button>
      </div>
      <div class="date-row">
        <label>From <input type="date" id="filter-from"></label>
        <label>To <input type="date" id="filter-to"></label>
      </div>
    </div>

    <div class="filter-block">
      <div class="filter-block-label">Outcome</div>
      <div class="check-grid">
        <label class="check-row"><input type="checkbox" class="f-outcome" value="resolved" checked> Resolved</label>
        <label class="check-row"><input type="checkbox" class="f-outcome" value="followup" checked> Follow-up needed</label>
      </div>
    </div>

    <div class="filter-block">
      <div class="filter-block-label">Confidence</div>
      <div class="check-grid">
        <label class="check-row"><input type="checkbox" class="f-confidence" value="1" checked> High</label>
        <label class="check-row"><input type="checkbox" class="f-confidence" value="2" checked> Medium</label>
        <label class="check-row"><input type="checkbox" class="f-confidence" value="3" checked> Low</label>
        <label class="check-row"><input type="checkbox" class="f-confidence" value="0" checked> Unrated</label>
      </div>
    </div>

    <div class="filter-block">
      <div class="filter-block-label">Escalation</div>
      <div class="check-grid">
        <label class="check-row"><input type="checkbox" class="f-escalation" value="yes" checked> Escalated</label>
        <label class="check-row"><input type="checkbox" class="f-escalation" value="no" checked> Not escalated</label>
      </div>
    </div>

    <div class="filter-block">
      <div class="filter-block-label">Sort &amp; page size</div>
      <div class="date-row">
        <label>Sort by
          <select id="sort-by">
            <option value="newest" selected>Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="lengthiest">Longest call first</option>
          </select>
        </label>
        <label>Show
          <select id="page-size">
            <option value="25">25</option>
            <option value="50" selected>50</option>
            <option value="100">100</option>
            <option value="0">All</option>
          </select>
        </label>
      </div>
    </div>
  </div>
  <div class="drawer-foot">
    <button class="btn btn-ghost" id="filter-clear">Clear all</button>
    <button class="btn btn-primary" id="filter-apply">Apply filters</button>
  </div>
</aside>

<div class="modal-scrim" id="modal-scrim">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <div class="modal-head">
      <div>
        <h3 id="modal-title">Transcript</h3>
        <div class="modal-meta" id="modal-meta"></div>
      </div>
      <button class="btn btn-ghost" id="modal-close" aria-label="Close transcript">✕</button>
    </div>
    <div class="modal-body" id="modal-body"></div>
    <div class="modal-foot">
      <button class="btn btn-ghost" id="modal-close-2">Close</button>
    </div>
  </div>
</div>
"""

_PAGINATION_BAR = """
<div class="pagination">
  <button type="button" class="btn btn-ghost" id="page-prev">&larr; Prev</button>
  <span id="page-indicator"></span>
  <button type="button" class="btn btn-ghost" id="page-next">Next &rarr;</button>
</div>
"""

_FILTER_SCRIPT = """
<script>
(function() {
  var fromEl = document.getElementById('filter-from');
  var toEl = document.getElementById('filter-to');
  var presetRow = document.getElementById('preset-row');
  var resultCountEl = document.getElementById('result-count');
  var filterCountEl = document.getElementById('filter-count');
  var chipEl = document.getElementById('topic-filter-chip');
  var sortEl = document.getElementById('sort-by');
  var pageSizeEl = document.getElementById('page-size');
  var prevBtn = document.getElementById('page-prev');
  var nextBtn = document.getElementById('page-next');
  var pageIndicatorEl = document.getElementById('page-indicator');
  var tbody = document.querySelector('#call-table tbody');
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('#call-table tbody tr[data-date]')
  );
  var selectedTopic = null;
  var matchedRows = [];
  var currentPage = 1;
  var settingDatesProgrammatically = false;

  function checkedValues(selector) {
    return Array.prototype.slice.call(document.querySelectorAll(selector + ':checked'))
      .map(function(el) { return el.value; });
  }

  function setChecked(selector, values) {
    document.querySelectorAll(selector).forEach(function(el) {
      el.checked = values.indexOf(el.value) !== -1;
    });
  }

  function computeMatches() {
    var from = fromEl.value;
    var to = toEl.value;
    var outcomes = checkedValues('.f-outcome');
    var confidences = checkedValues('.f-confidence');
    var escalations = checkedValues('.f-escalation');
    return rows.filter(function(row) {
      var date = row.getAttribute('data-date');
      if (date) {
        if (from && date < from) return false;
        if (to && date > to) return false;
      }
      if (outcomes.indexOf(row.getAttribute('data-outcome')) === -1) return false;
      if (confidences.indexOf(row.getAttribute('data-confidence')) === -1) return false;
      if (escalations.indexOf(row.getAttribute('data-escalation')) === -1) return false;
      if (selectedTopic) {
        var rowTopics = (row.getAttribute('data-topics') || '').split('|');
        if (rowTopics.indexOf(selectedTopic) === -1) return false;
      }
      return true;
    });
  }

  function byTimestamp(sortBy) {
    // Rows with an unknown timestamp always sink to the bottom, in either
    // direction, rather than winning "oldest first" by empty-string quirk.
    return function(a, b) {
      var ta = a.getAttribute('data-ts') || '';
      var tb = b.getAttribute('data-ts') || '';
      if (!ta && !tb) return 0;
      if (!ta) return 1;
      if (!tb) return -1;
      return sortBy === 'oldest' ? ta.localeCompare(tb) : tb.localeCompare(ta);
    };
  }

  function sortMatches(list) {
    var sortBy = sortEl.value;
    var sorted = list.slice();
    if (sortBy === 'lengthiest') {
      sorted.sort(function(a, b) {
        return parseFloat(b.getAttribute('data-duration')) - parseFloat(a.getAttribute('data-duration'));
      });
    } else {
      sorted.sort(byTimestamp(sortBy));
    }
    return sorted;
  }

  function reorderDom(list) {
    list.forEach(function(row) { tbody.appendChild(row); });
  }

  function updateFilterCount() {
    var active = 0;
    if (checkedValues('.f-outcome').length < 2) active++;
    if (checkedValues('.f-confidence').length < 4) active++;
    if (checkedValues('.f-escalation').length < 2) active++;
    if (fromEl.value || toEl.value) active++;
    filterCountEl.textContent = active;
    filterCountEl.hidden = active === 0;
  }

  function renderPage() {
    var total = matchedRows.length;
    var pageSize = parseInt(pageSizeEl.value, 10) || 0;
    var totalPages = pageSize ? Math.max(Math.ceil(total / pageSize), 1) : 1;
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;
    var start = pageSize ? (currentPage - 1) * pageSize : 0;
    var end = pageSize ? start + pageSize : total;
    var visible = matchedRows.slice(start, end);
    var visibleSet = new Set(visible);

    rows.forEach(function(row) {
      row.style.display = visibleSet.has(row) ? '' : 'none';
    });

    resultCountEl.textContent = total === 0
      ? '0 of ' + rows.length + ' shown'
      : (start + 1) + '\\u2013' + Math.min(end, total) + ' of ' + total + ' matched (' + rows.length + ' total)';
    pageIndicatorEl.textContent = 'Page ' + currentPage + ' of ' + totalPages;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
    updateFilterCount();
  }

  function applyFilters() {
    matchedRows = sortMatches(computeMatches());
    reorderDom(matchedRows);
    currentPage = 1;
    renderPage();
  }

  function setTopic(topic) {
    selectedTopic = topic;
    document.querySelectorAll('.badge-clickable').forEach(function(el) {
      el.classList.toggle('topic-active', el.getAttribute('data-topic') === topic);
    });
    if (topic) {
      chipEl.textContent = 'Topic: ' + topic + '  \\u2715';
      chipEl.hidden = false;
    } else {
      chipEl.hidden = true;
    }
    applyFilters();
  }

  function scrollToTable() {
    var table = document.getElementById('call-table');
    if (table) table.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  function setPreset(key) {
    document.querySelectorAll('.preset-pill').forEach(function(p) {
      p.classList.toggle('active', p.getAttribute('data-preset') === key);
    });
  }

  function applyPreset(key) {
    settingDatesProgrammatically = true;
    if (key === 'week') {
      fromEl.value = presetRow.getAttribute('data-week');
      toEl.value = presetRow.getAttribute('data-today');
    } else if (key === 'month') {
      fromEl.value = presetRow.getAttribute('data-month');
      toEl.value = presetRow.getAttribute('data-today');
    } else if (key === '30d') {
      fromEl.value = presetRow.getAttribute('data-30d');
      toEl.value = presetRow.getAttribute('data-today');
    } else {
      fromEl.value = '';
      toEl.value = '';
    }
    settingDatesProgrammatically = false;
    setPreset(key);
    applyFilters();
  }

  document.querySelectorAll('.preset-pill').forEach(function(p) {
    p.addEventListener('click', function() { applyPreset(p.getAttribute('data-preset')); });
  });

  document.querySelectorAll('.f-outcome, .f-confidence, .f-escalation').forEach(function(el) {
    el.addEventListener('change', applyFilters);
  });
  fromEl.addEventListener('change', function() {
    if (!settingDatesProgrammatically) setPreset('custom');
    applyFilters();
  });
  toEl.addEventListener('change', function() {
    if (!settingDatesProgrammatically) setPreset('custom');
    applyFilters();
  });
  sortEl.addEventListener('change', applyFilters);
  pageSizeEl.addEventListener('change', function() { currentPage = 1; renderPage(); });
  prevBtn.addEventListener('click', function() { currentPage -= 1; renderPage(); });
  nextBtn.addEventListener('click', function() { currentPage += 1; renderPage(); });

  document.getElementById('filter-clear').addEventListener('click', function() {
    fromEl.value = '';
    toEl.value = '';
    document.querySelectorAll('.f-outcome, .f-confidence, .f-escalation').forEach(function(el) { el.checked = true; });
    sortEl.value = 'newest';
    setPreset('all');
    setTopic(null);
  });
  chipEl.addEventListener('click', function() { setTopic(null); });

  document.querySelectorAll('.badge-clickable').forEach(function(el) {
    el.addEventListener('click', function() {
      var topic = el.getAttribute('data-topic');
      setTopic(selectedTopic === topic ? null : topic);
      scrollToTable();
    });
  });

  var followupsTile = document.getElementById('tile-followups');
  if (followupsTile) {
    followupsTile.addEventListener('click', function() {
      fromEl.value = followupsTile.getAttribute('data-from');
      toEl.value = followupsTile.getAttribute('data-to');
      setPreset('custom');
      setChecked('.f-outcome', ['followup']);
      setChecked('.f-confidence', ['0', '1', '2', '3']);
      applyFilters();
      openDrawer();
      scrollToTable();
    });
  }

  var drawer = document.getElementById('drawer');
  var scrim = document.getElementById('scrim');
  function openDrawer() { drawer.classList.add('open'); scrim.classList.add('open'); drawer.setAttribute('aria-hidden', 'false'); }
  function closeDrawer() { drawer.classList.remove('open'); scrim.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); }
  document.getElementById('filter-open').addEventListener('click', openDrawer);
  document.getElementById('drawer-close').addEventListener('click', closeDrawer);
  document.getElementById('filter-apply').addEventListener('click', closeDrawer);
  scrim.addEventListener('click', closeDrawer);

  var modalScrim = document.getElementById('modal-scrim');
  var modalTitle = document.getElementById('modal-title');
  var modalMeta = document.getElementById('modal-meta');
  var modalBody = document.getElementById('modal-body');

  function openTranscript(btn) {
    var caller = btn.getAttribute('data-caller');
    modalTitle.textContent = caller + "'s call";
    modalMeta.textContent = btn.getAttribute('data-when') + ' \\u00b7 ' + btn.getAttribute('data-duration') + ' \\u00b7 ' + btn.getAttribute('data-phone');
    modalBody.innerHTML = '';
    btn.getAttribute('data-transcript').split('\\n').forEach(function(line) {
      if (!line.trim()) return;
      var isCaller = line.indexOf('Caller:') === 0;
      var speaker = isCaller ? 'Caller' : (line.indexOf(':') !== -1 ? line.slice(0, line.indexOf(':')) : 'Agent');
      var text = line.indexOf(':') !== -1 ? line.slice(line.indexOf(':') + 1).trim() : line;
      var turn = document.createElement('div');
      turn.className = 'turn ' + (isCaller ? 'turn-caller' : 'turn-bot');
      var label = document.createElement('div');
      label.className = 'turn-label';
      label.textContent = speaker;
      turn.appendChild(label);
      turn.appendChild(document.createTextNode(text));
      modalBody.appendChild(turn);
    });
    modalScrim.classList.add('open');
  }
  function closeTranscript() { modalScrim.classList.remove('open'); }

  tbody.addEventListener('click', function(e) {
    var btn = e.target.closest('.view-transcript');
    if (!btn) return;
    openTranscript(btn);
  });
  document.getElementById('modal-close').addEventListener('click', closeTranscript);
  document.getElementById('modal-close-2').addEventListener('click', closeTranscript);
  modalScrim.addEventListener('click', function(e) { if (e.target === modalScrim) closeTranscript(); });
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    closeDrawer();
    closeTranscript();
  });

  if (rows.length) applyFilters();
})();
</script>
"""


def render_call_table(calls: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    columns = ["Call time", "Caller", "Topic", "Outcome", "Recording", "Transcript", "Confidence", "Escalation"]
    header = "".join(f"<th>{c}</th>" for c in columns)
    rows = "".join(render_call_row(c) for c in calls) or (
        f"<tr><td colspan='{len(columns)}'><div class='empty-note'>No calls logged yet.</div></td></tr>"
    )
    drawer = _FILTER_DRAWER.format(
        week_start=stats["this_week_start_iso"],
        month_start=stats["this_month_start_iso"],
        last_30d_start=stats["last_30_days_start_iso"],
        today=stats["today_iso"],
    )
    return f"""<div class="card table-card">
    <div class="table-head">
      <h3>Recent calls</h3>
      <div class="table-tools">
        <span class="topic-chip" id="topic-filter-chip" hidden></span>
        <span class="result-count" id="result-count"></span>
        <button type="button" class="btn filter-btn" id="filter-open">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 6h16M7 12h10M10 18h4"/></svg>
          Filter &amp; sort
          <span class="filter-count" id="filter-count" hidden>0</span>
        </button>
      </div>
    </div>
    <div class="table-scroll"><table id="call-table">
      <thead><tr>{header}</tr></thead><tbody>{rows}</tbody>
    </table></div>
    {_PAGINATION_BAR}
  </div>{drawer}{_FILTER_SCRIPT}"""


def render_restaurant_page(
    cfg: RestaurantConfig, calls: list[dict[str, Any]], stats: dict[str, Any]
) -> str:
    body = f"""
  <div class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <span class="brand-mark">{escape(cfg.display_name)}</span>
        <span class="brand-sub">Call desk</span>
      </div>
      <div class="topbar-actions">
        <a href="{escape(cfg.admin_path)}">Refresh</a>
        <a href="{escape(cfg.admin_path.rstrip('/'))}/contacts.csv">Export contacts (CSV)</a>
      </div>
    </div>
  </div>

  <div class="shell">
    <div class="page-head">
      <h1>Front of house</h1>
      <p>{len(calls)} call(s) logged. Data may be up to 20s stale (short cache to avoid re-reading the Sheet on every request).</p>
    </div>

    <section>
      {render_kpi_row(stats)}
    </section>

    <section>
      <div class="split">
        <div class="card">
          <h3>Call outcome, this month</h3>
          {render_outcome_donut(stats["resolved_this_month"], stats["followups_needed_this_month"])}
        </div>
        <div class="card">
          <h3>What callers ask about</h3>
          {render_topic_breakdown(stats["topic_counts"])}
        </div>
      </div>
    </section>

    <section>
      <div class="split charts-split">
        <div class="card">
          <h3>Calls by hour of day (IST)</h3>
          {render_hour_chart(stats["hour_counts"])}
        </div>
        <div class="card">
          <h3>Calls by day of week</h3>
          {render_day_chart(stats["day_counts"])}
        </div>
      </div>
    </section>

    <section>
      {render_call_table(calls, stats)}
    </section>
  </div>
"""
    return _page(f"{cfg.display_name} — Dashboard", body)


def render_super_admin_page(entries: list[tuple[RestaurantConfig, dict[str, Any]]]) -> str:
    links = "".join(
        f'<a href="{escape(cfg.admin_path)}">{escape(cfg.display_name)} →</a>' for cfg, _ in entries
    )
    combined_calls = sum(stats["total_calls_this_month"] for _, stats in entries)
    combined_minutes = sum(stats["minutes_used_this_month"] for _, stats in entries)

    rows = "".join(
        f"""<div class="tile">
      <div class="value">{escape(cfg.display_name)}</div>
      <div class="label">{stats["total_calls_this_month"]} calls this month &middot; {stats["followups_needed_this_month"]} follow-up(s) needed</div>
      {render_cap_bar(stats["minutes_used_this_month"], stats["minutes_allowed_per_month"], stats["minutes_remaining_this_month"], stats["minutes_used_pct"], stats["minutes_pct_change"])}
    </div>"""
        for cfg, stats in entries
    )

    body = f"""
  <h2>All restaurants</h2>
  <p class="meta restaurant-links">{links}</p>
  <p class="meta">{combined_calls} call(s) this month combined · {combined_minutes:.0f} min combined</p>
  <div class="tiles">{rows}</div>
"""
    return _page("Admin — All Restaurants", body)


def render_home_page(config: AppConfig) -> str:
    """Testing/staging control panel: every route this service exposes, in
    one place, so testers don't need to know or guess URLs."""
    restaurant_tiles = "".join(
        f"""<a class="tile tile-link" href="{escape(cfg.admin_path)}">
      <div class="value">{escape(cfg.display_name)}</div>
      <div class="label">Dashboard &middot; {escape(cfg.admin_path)}</div>
    </a>"""
        for cfg in config.restaurants.values()
    )
    contact_export_items = "".join(
        f'<li><a href="{escape(cfg.admin_path.rstrip("/"))}/contacts.csv">'
        f'{escape(cfg.display_name)} — contacts.csv</a></li>'
        for cfg in config.restaurants.values()
    )
    body = f"""
  <h2>Restaurant Voice Agent — Control Panel</h2>
  <p class="meta">Every route this service exposes, in one place. Each dashboard below has its own login.</p>

  <h3>Dashboards</h3>
  <div class="tiles">
    <a class="tile tile-link" href="/admin">
      <div class="value">All restaurants</div>
      <div class="label">Super-admin &middot; /admin</div>
    </a>
    {restaurant_tiles}
  </div>

  <h3>Other routes</h3>
  <ul class="route-list">
    <li><a href="/healthz">/healthz</a> — service status + restaurant registry (JSON, no login)</li>
    {contact_export_items}
    <li><a href="/docs">/docs</a> — FastAPI interactive API docs (try requests directly)</li>
    <li><a href="/redoc">/redoc</a> — FastAPI API reference</li>
  </ul>
"""
    return _page("Control Panel — Restaurant Voice Agent", body)
