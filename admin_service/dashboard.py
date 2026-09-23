"""HTML rendering for the dashboard pages. Small composable pieces (one
job each) rather than one long page-builder — see app/admin/routes.py for
the CSS/table styling this borrows.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from admin_service.config import AppConfig, RestaurantConfig
from admin_service.stats import categorize_topics

_IST = ZoneInfo("Asia/Kolkata")

_STYLE = """
:root { --border: #e2e2e2; --bg-alt: #fafafa; --text-muted: #6b7280; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", system-ui, sans-serif; margin: 0; padding: 2rem; color: #1a1a1a; background: #f5f6f8; }
h2 { margin: 0 0 0.25rem; font-size: 1.4rem; }
h3 { margin: 1.75rem 0 0.75rem; font-size: 1.05rem; }
.meta { color: var(--text-muted); font-size: 0.85rem; margin: 0 0 1.25rem; }
a { color: #2563eb; }
.table-wrap { background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; min-width: 900px; }
th, td { border-bottom: 1px solid var(--border); padding: 10px 12px; text-align: left; vertical-align: top; }
th { background: var(--bg-alt); position: sticky; top: 0; font-weight: 600; color: #374151; white-space: nowrap; }
tr:hover td { background: #fbfbfd; }
.nowrap { white-space: nowrap; }
.muted { color: var(--text-muted); font-size: 0.78rem; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; margin: 1px 3px 1px 0; }
.badge-topic { background: #eef2ff; color: #3730a3; }
.badge-neutral { background: #f0f0f0; color: #6b7280; }
.badge-green { background: #dcfce7; color: #166534; }
.badge-yellow { background: #fef9c3; color: #854d0e; }
.badge-red { background: #fee2e2; color: #991b1b; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 1.5rem; }
.tile { background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); padding: 14px 18px; min-width: 140px; }
.tile .value { font-size: 1.5rem; font-weight: 700; }
.tile .label { color: var(--text-muted); font-size: 0.78rem; }
.tile-clickable { cursor: pointer; }
.tile-clickable:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.14); }
.badge-clickable { cursor: pointer; }
.badge-clickable:hover { filter: brightness(0.95); }
.badge-clickable.badge-active { outline: 2px solid #3730a3; }
.topic-filter-chip { display: inline-flex; align-items: center; gap: 6px; background: #eef2ff; color: #3730a3; border-radius: 999px; padding: 2px 10px; font-size: 0.78rem; font-weight: 600; cursor: pointer; }
.topic-filter-chip[hidden] { display: none; }
.cap-bar-track { background: #e5e7eb; border-radius: 999px; height: 10px; width: 100%; max-width: 320px; overflow: hidden; }
.cap-bar-fill { height: 100%; border-radius: 999px; }
.charts-row { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 20px; }
.chart-col { flex: 0 1 auto; min-width: 0; }
.chart-col h3 { margin-top: 0; }
.hour-chart { display: flex; align-items: flex-end; gap: 3px; height: 160px; max-width: 640px; padding-top: 18px; overflow-x: auto; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.day-chart { max-width: 280px; }
.hour-col { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; flex: 1 0 20px; height: 100%; }
.hour-col-count { font-size: 0.65rem; color: var(--text-muted); height: 14px; }
.hour-col-bar { width: 8px; min-height: 4px; background: #6366f1; border-radius: 999px; }
.hour-col-label { font-size: 0.65rem; color: var(--text-muted); margin-top: 4px; }
.restaurant-links a { display: inline-block; margin-right: 14px; font-weight: 600; }
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); padding: 12px 16px; margin-bottom: 1.25rem; font-size: 0.85rem; }
.filter-group { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.filter-group label { display: flex; align-items: center; gap: 4px; white-space: nowrap; cursor: pointer; }
.filter-label { font-weight: 600; color: #374151; margin-right: 2px; }
.filters input[type="date"] { border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; font-size: 0.85rem; }
.filters select { border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; font-size: 0.85rem; }
.filters button { border: 1px solid var(--border); background: #f5f6f8; border-radius: 6px; padding: 5px 10px; font-size: 0.8rem; cursor: pointer; }
.filters button:hover { background: #eceef1; }
.pagination { display: flex; align-items: center; justify-content: center; gap: 14px; padding: 12px 0 2px; }
.pagination button { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 5px 12px; font-size: 0.8rem; cursor: pointer; }
.pagination button:hover:not(:disabled) { background: #eceef1; }
.pagination button:disabled { opacity: 0.4; cursor: default; }
.tile-link { text-decoration: none; color: inherit; display: block; }
.tile-link:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.14); }
.route-list { list-style: none; margin: 0 0 1.5rem; padding: 6px 20px; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); font-size: 0.85rem; }
.route-list li { padding: 8px 0; border-bottom: 1px solid var(--border); }
.route-list li:last-child { border-bottom: none; }
.trend { font-size: 0.75rem; font-weight: 700; margin-top: 6px; }
.trend-up { color: #16a34a; }
.trend-down { color: #dc2626; }
.trend-flat { color: var(--text-muted); }
.trend-note { font-weight: 400; color: var(--text-muted); }
.summary-row { display: flex; flex-wrap: wrap; gap: 20px; align-items: stretch; }
.summary-main { flex: 3 1 480px; min-width: 0; }
.summary-main .tiles { margin-bottom: 0; }
.summary-side { flex: 1 1 260px; max-width: 320px; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); padding: 16px 20px; margin-bottom: 1.5rem; display: flex; flex-direction: column; justify-content: center; }
.donut-heading { font-weight: 600; color: #374151; font-size: 0.85rem; margin-bottom: 10px; }
.donut-wrap { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }
.tile-minutes { min-width: 240px; }
.donut { width: 120px; height: 120px; border-radius: 50%; position: relative; flex-shrink: 0; }
.donut::after { content: ""; position: absolute; inset: 22px; background: #fff; border-radius: 50%; }
.donut-center { position: absolute; inset: 0; z-index: 1; display: flex; align-items: center; justify-content: center; flex-direction: column; text-align: center; }
.donut-center .value { font-size: 1.15rem; font-weight: 700; }
.donut-center .label { font-size: 0.62rem; color: var(--text-muted); }
.donut-legend { display: flex; flex-direction: column; gap: 10px; font-size: 0.85rem; }
.donut-legend-item { display: flex; align-items: center; gap: 8px; }
.donut-legend-item .count { font-weight: 700; }
.donut-swatch { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
.dropdown-multiselect { position: relative; display: inline-block; }
.dropdown-toggle { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 5px 10px; font-size: 0.85rem; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.dropdown-toggle:hover { background: #f5f6f8; }
.dropdown-arrow { font-size: 0.7rem; color: var(--text-muted); }
.dropdown-panel { position: absolute; top: calc(100% + 4px); left: 0; background: #fff; border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.12); padding: 8px 12px; z-index: 20; display: flex; flex-direction: column; gap: 6px; min-width: 140px; }
.dropdown-panel[hidden] { display: none; }
.dropdown-panel label { display: flex; align-items: center; gap: 6px; white-space: nowrap; cursor: pointer; }
"""

_CONFIDENCE_BADGES = {
    0: ("—", "badge-neutral"),
    1: ("High", "badge-green"),
    2: ("Medium", "badge-yellow"),
    3: ("Low", "badge-red"),
}


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>{_STYLE}</style></head><body>{body}</body></html>"""


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
        return "#991b1b"
    if pct >= 80:
        return "#b45309"
    return "#16a34a"


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


def render_stat_tiles(stats: dict[str, Any]) -> str:
    minutes_pct = stats["minutes_used_pct"]
    minutes_fill_pct = min(minutes_pct, 100)
    minutes_color = _cap_color(minutes_pct)
    minutes_tile = f"""<div class="tile tile-minutes">
    <div class="value">{stats['minutes_used_this_month']:.0f} / {stats['minutes_allowed_per_month']}</div>
    <div class="label">Minutes used this month</div>
    <div class="cap-bar-track"><div class="cap-bar-fill" style="width:{minutes_fill_pct:.0f}%;background:{minutes_color}"></div></div>
    <span class="muted">{minutes_pct:.0f}% used &middot; {stats['minutes_remaining_this_month']:.0f} min remaining</span>
    {render_trend(stats['minutes_pct_change'])}</div>"""
    week_tile = f"""<div class="tile">
    <div class="value">{stats['total_calls_this_week']}</div>
    <div class="label">Calls this week</div></div>"""
    month_tile = f"""<div class="tile">
    <div class="value">{stats['total_calls_this_month']}</div>
    <div class="label">Calls this month</div>
    {render_trend(stats['calls_pct_change'])}</div>"""
    all_time_tile = f"""<div class="tile">
    <div class="value">{stats['total_calls_all_time']}</div>
    <div class="label">Calls all-time</div></div>"""
    after = [
        ("Avg call length", f"{stats['avg_call_minutes']:.1f} min"),
        ("Shortest / longest", f"{stats['min_call_minutes']:.1f} / {stats['max_call_minutes']:.1f} min"),
    ]
    after_html = "".join(
        f'<div class="tile"><div class="value">{v}</div><div class="label">{escape(k)}</div></div>'
        for k, v in after
    )
    followups_tile = f"""<div class="tile tile-clickable" id="tile-followups"
    data-from="{stats['this_month_start_iso']}" data-to="{stats['today_iso']}"
    title="Click to filter the call log below to this month's follow-ups">
    <div class="value">{stats['followups_needed_this_month']}</div>
    <div class="label">Follow-ups needed (this month)</div>
    {render_trend(stats['followups_pct_change'])}</div>"""
    return (
        f'<div class="tiles">{minutes_tile}{week_tile}{month_tile}{all_time_tile}'
        f'{followups_tile}{after_html}</div>'
    )


def render_outcome_donut(resolved: int, followup: int) -> str:
    """Call outcome this month, Resolved vs Follow-up needed, as a donut —
    pure CSS conic-gradient, no charting library."""
    total = resolved + followup
    if total == 0:
        return '<p class="muted">No calls logged yet this month.</p>'
    resolved_pct = resolved / total * 100
    gradient = f"conic-gradient(#16a34a 0% {resolved_pct:.2f}%, #dc2626 {resolved_pct:.2f}% 100%)"
    return f"""<div class="donut-wrap">
  <div class="donut" style="background:{gradient}">
    <div class="donut-center">
      <div class="value">{resolved_pct:.0f}%</div>
      <div class="label">Resolved</div>
    </div>
  </div>
  <div class="donut-legend">
    <div class="donut-legend-item"><span class="donut-swatch" style="background:#16a34a"></span> Resolved &middot; <span class="count">{resolved}</span></div>
    <div class="donut-legend-item"><span class="donut-swatch" style="background:#dc2626"></span> Follow-up needed &middot; <span class="count">{followup}</span></div>
  </div>
</div>"""


def render_topic_breakdown(topic_counts: dict[str, int]) -> str:
    if not topic_counts:
        return '<p class="muted">No categorized topics yet.</p>'
    badges = "".join(
        f'<span class="badge badge-topic badge-clickable" data-topic="{escape(name)}" '
        f'title="Click to filter the call log below to this topic">{escape(name)} · {count}</span>'
        for name, count in topic_counts.items()
    )
    return f'<p id="topic-badges">{badges}</p>'


def render_hour_chart(hour_counts: list[int]) -> str:
    if not any(hour_counts):
        return '<p class="muted">No call-time data yet.</p>'
    peak = max(hour_counts) or 1
    cols = "".join(
        f'<div class="hour-col" title="{h:02d}:00 &middot; {count} call(s)">'
        f'<span class="hour-col-count">{count or ""}</span>'
        f'<div class="hour-col-bar" style="height:{(count / peak) * 100:.0f}%"></div>'
        f'<span class="hour-col-label">{h}</span>'
        f'</div>'
        for h, count in enumerate(hour_counts)
    )
    return f'<div class="hour-chart">{cols}</div>'


_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def render_day_chart(day_counts: list[int]) -> str:
    if not any(day_counts):
        return '<p class="muted">No call-time data yet.</p>'
    peak = max(day_counts) or 1
    cols = "".join(
        f'<div class="hour-col" title="{_DAY_LABELS[d]} &middot; {count} call(s)">'
        f'<span class="hour-col-count">{count or ""}</span>'
        f'<div class="hour-col-bar" style="height:{(count / peak) * 100:.0f}%"></div>'
        f'<span class="hour-col-label">{_DAY_LABELS[d]}</span>'
        f'</div>'
        for d, count in enumerate(day_counts)
    )
    return f'<div class="hour-chart day-chart">{cols}</div>'


def render_call_row(call: dict[str, Any]) -> str:
    date_str, time_str, iso_date = _format_datetime(call["timestamp"])
    duration_str = _format_duration(call.get("duration_secs"))
    outcome_key = "followup" if call["needs_followup"] else "resolved"
    confidence_key = call["confidence_rank"]
    categories = categorize_topics(call["topics"])
    topic_html = "".join(f'<span class="badge badge-topic">{escape(c)}</span>' for c in categories) or "—"

    outcome_badge = (
        '<span class="badge badge-red">⚠ Follow-up</span>'
        if call["needs_followup"]
        else '<span class="badge badge-green">✓ Resolved</span>'
    )

    if call["recording_url"]:
        recording_html = f'<a href="{escape(call["recording_url"])}" target="_blank" rel="noopener">▶ Listen</a>'
    else:
        recording_html = "—"

    conf_label, conf_class = _CONFIDENCE_BADGES.get(call["confidence_rank"], _CONFIDENCE_BADGES[0])
    topics_attr = escape("|".join(categories))
    ts_attr = escape(call["timestamp"] or "")
    duration_attr = _duration_seconds(call.get("duration_secs"))
    escalation_key = "yes" if call["escalated"] else "no"

    escalation_badge = (
        '<span class="badge badge-red">⚠ Escalated</span>'
        if call["escalated"]
        else '<span class="badge badge-neutral">—</span>'
    )

    return f"""<tr data-date="{iso_date}" data-ts="{ts_attr}" data-duration="{duration_attr}" data-outcome="{outcome_key}" data-confidence="{confidence_key}" data-escalation="{escalation_key}" data-topics="{topics_attr}">
    <td class="nowrap">{duration_str}<br><span class="muted">{date_str} {time_str}</span></td>
    <td>{escape(call["caller_name"] or "—")}</td>
    <td class="nowrap">{escape(call["caller_phone"] or "—")}</td>
    <td>{topic_html}</td>
    <td>{outcome_badge}</td>
    <td>{recording_html}</td>
    <td><span class="badge {conf_class}">{conf_label}</span></td>
    <td>{escalation_badge}</td>
  </tr>"""


_FILTER_BAR = """
<div class="filters">
  <div class="filter-group">
    <label>Date range
      <select id="date-preset" data-week="{week_start}" data-month="{month_start}" data-30d="{last_30d_start}" data-today="{today}">
        <option value="custom" selected>Custom</option>
        <option value="week">This week</option>
        <option value="month">This month</option>
        <option value="30d">Last 30 days</option>
        <option value="all">All time</option>
      </select>
    </label>
  </div>
  <div class="filter-group">
    <label>From <input type="date" id="filter-from"></label>
    <label>To <input type="date" id="filter-to"></label>
  </div>
  <div class="filter-group">
    <span class="filter-label">Outcome</span>
    <div class="dropdown-multiselect" id="outcome-dropdown">
      <button type="button" class="dropdown-toggle" id="outcome-toggle">All <span class="dropdown-arrow">&#9662;</span></button>
      <div class="dropdown-panel" id="outcome-panel" hidden>
        <label><input type="checkbox" class="f-outcome" value="resolved" checked> Resolved</label>
        <label><input type="checkbox" class="f-outcome" value="followup" checked> Follow-up needed</label>
      </div>
    </div>
  </div>
  <div class="filter-group">
    <span class="filter-label">Confidence</span>
    <div class="dropdown-multiselect" id="confidence-dropdown">
      <button type="button" class="dropdown-toggle" id="confidence-toggle">All <span class="dropdown-arrow">&#9662;</span></button>
      <div class="dropdown-panel" id="confidence-panel" hidden>
        <label><input type="checkbox" class="f-confidence" value="1" checked> High</label>
        <label><input type="checkbox" class="f-confidence" value="2" checked> Medium</label>
        <label><input type="checkbox" class="f-confidence" value="3" checked> Low</label>
        <label><input type="checkbox" class="f-confidence" value="0" checked> Unrated</label>
      </div>
    </div>
  </div>
  <div class="filter-group">
    <span class="filter-label">Escalation</span>
    <div class="dropdown-multiselect" id="escalation-dropdown">
      <button type="button" class="dropdown-toggle" id="escalation-toggle">All <span class="dropdown-arrow">&#9662;</span></button>
      <div class="dropdown-panel" id="escalation-panel" hidden>
        <label><input type="checkbox" class="f-escalation" value="yes" checked> Escalated</label>
        <label><input type="checkbox" class="f-escalation" value="no" checked> Not escalated</label>
      </div>
    </div>
  </div>
  <div class="filter-group">
    <label>Sort by
      <select id="sort-by">
        <option value="newest" selected>Newest first</option>
        <option value="oldest">Oldest first</option>
        <option value="lengthiest">Longest call first</option>
      </select>
    </label>
  </div>
  <div class="filter-group">
    <label>Show
      <select id="page-size">
        <option value="25">25</option>
        <option value="50" selected>50</option>
        <option value="100">100</option>
        <option value="0">All</option>
      </select>
    </label>
  </div>
  <button type="button" id="filter-clear">Clear filters</button>
  <span class="muted" id="filter-count"></span>
  <span class="topic-filter-chip" id="topic-filter-chip" hidden></span>
</div>
"""

_PAGINATION_BAR = """
<div class="pagination">
  <button type="button" id="page-prev">&larr; Prev</button>
  <span class="muted" id="page-indicator"></span>
  <button type="button" id="page-next">Next &rarr;</button>
</div>
"""

_FILTER_SCRIPT = """
<script>
(function() {
  var fromEl = document.getElementById('filter-from');
  var toEl = document.getElementById('filter-to');
  var presetEl = document.getElementById('date-preset');
  var countEl = document.getElementById('filter-count');
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

  function initMultiSelect(name, labels, order) {
    var dropdown = document.getElementById(name + '-dropdown');
    var toggle = document.getElementById(name + '-toggle');
    var panel = document.getElementById(name + '-panel');
    var selector = '.f-' + name;

    function updateLabel() {
      var selected = checkedValues(selector);
      var text;
      if (selected.length === 0) {
        text = 'None';
      } else if (selected.length === order.length) {
        text = 'All';
      } else {
        text = order
          .filter(function(v) { return selected.indexOf(v) !== -1; })
          .map(function(v) { return labels[v]; })
          .join(', ');
      }
      toggle.firstChild.textContent = text + ' ';
    }

    toggle.addEventListener('click', function(e) {
      e.stopPropagation();
      panel.hidden = !panel.hidden;
    });
    document.addEventListener('click', function(e) {
      if (!panel.hidden && !dropdown.contains(e.target)) {
        panel.hidden = true;
      }
    });
    document.querySelectorAll(selector).forEach(function(el) {
      el.addEventListener('change', updateLabel);
    });

    return updateLabel;
  }

  var updateOutcomeLabel = initMultiSelect('outcome', {resolved: 'Resolved', followup: 'Follow-up needed'}, ['resolved', 'followup']);
  var updateConfidenceLabel = initMultiSelect('confidence', {'1': 'High', '2': 'Medium', '3': 'Low', '0': 'Unrated'}, ['1', '2', '3', '0']);
  var updateEscalationLabel = initMultiSelect('escalation', {yes: 'Escalated', no: 'Not escalated'}, ['yes', 'no']);

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

    countEl.textContent = total === 0
      ? '0 of ' + rows.length + ' shown'
      : 'Showing ' + (start + 1) + '\\u2013' + Math.min(end, total) + ' of ' + total + ' matched (' + rows.length + ' total)';
    pageIndicatorEl.textContent = 'Page ' + currentPage + ' of ' + totalPages;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
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
      el.classList.toggle('badge-active', el.getAttribute('data-topic') === topic);
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

  function applyPreset() {
    var val = presetEl.value;
    if (val === 'custom') return;
    settingDatesProgrammatically = true;
    if (val === 'week') {
      fromEl.value = presetEl.getAttribute('data-week');
      toEl.value = presetEl.getAttribute('data-today');
    } else if (val === 'month') {
      fromEl.value = presetEl.getAttribute('data-month');
      toEl.value = presetEl.getAttribute('data-today');
    } else if (val === '30d') {
      fromEl.value = presetEl.getAttribute('data-30d');
      toEl.value = presetEl.getAttribute('data-today');
    } else if (val === 'all') {
      fromEl.value = '';
      toEl.value = '';
    }
    settingDatesProgrammatically = false;
    applyFilters();
  }

  document.querySelectorAll('.f-outcome, .f-confidence, .f-escalation').forEach(function(el) {
    el.addEventListener('change', applyFilters);
  });
  fromEl.addEventListener('change', function() {
    if (!settingDatesProgrammatically) presetEl.value = 'custom';
    applyFilters();
  });
  toEl.addEventListener('change', function() {
    if (!settingDatesProgrammatically) presetEl.value = 'custom';
    applyFilters();
  });
  presetEl.addEventListener('change', applyPreset);
  sortEl.addEventListener('change', applyFilters);
  pageSizeEl.addEventListener('change', function() { currentPage = 1; renderPage(); });
  prevBtn.addEventListener('click', function() { currentPage -= 1; renderPage(); });
  nextBtn.addEventListener('click', function() { currentPage += 1; renderPage(); });

  document.getElementById('filter-clear').addEventListener('click', function() {
    fromEl.value = '';
    toEl.value = '';
    presetEl.value = 'all';
    document.querySelectorAll('.f-outcome, .f-confidence, .f-escalation').forEach(function(el) { el.checked = true; });
    updateOutcomeLabel();
    updateConfidenceLabel();
    updateEscalationLabel();
    sortEl.value = 'newest';
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
      presetEl.value = 'custom';
      setChecked('.f-outcome', ['followup']);
      setChecked('.f-confidence', ['0', '1', '2', '3']);
      updateOutcomeLabel();
      updateConfidenceLabel();
      applyFilters();
      scrollToTable();
    });
  }

  updateConfidenceLabel();
  if (rows.length) applyFilters();
})();
</script>
"""


def render_call_table(calls: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    columns = ["Call Time", "Caller", "Phone", "Topic", "Outcome", "Recording", "Confidence", "Escalation"]
    header = "".join(f"<th>{c}</th>" for c in columns)
    rows = "".join(render_call_row(c) for c in calls) or (
        f"<tr><td colspan='{len(columns)}'>No calls logged yet.</td></tr>"
    )
    filter_bar = _FILTER_BAR.format(
        week_start=stats["this_week_start_iso"],
        month_start=stats["this_month_start_iso"],
        last_30d_start=stats["last_30_days_start_iso"],
        today=stats["today_iso"],
    )
    return f"""{filter_bar}<div class="table-wrap"><table id="call-table">
    <thead><tr>{header}</tr></thead><tbody>{rows}</tbody>
  </table></div>{_PAGINATION_BAR}{_FILTER_SCRIPT}"""


def render_restaurant_page(
    cfg: RestaurantConfig, calls: list[dict[str, Any]], stats: dict[str, Any]
) -> str:
    body = f"""
  <h2>{escape(cfg.display_name)}</h2>
  <p class="meta">{len(calls)} call(s) logged. Data may be up to 20s stale (short cache to avoid re-reading the Sheet on every request). &middot;
    <a href="{escape(cfg.admin_path)}">Refresh</a> &middot;
    <a href="{escape(cfg.admin_path.rstrip('/'))}/contacts.csv">Export contacts (CSV)</a></p>
  <div class="summary-row">
    <div class="summary-main">
      {render_stat_tiles(stats)}
    </div>
    <div class="summary-side">
      <div class="donut-heading">Call outcome (this month)</div>
      {render_outcome_donut(stats["resolved_this_month"], stats["followups_needed_this_month"])}
    </div>
  </div>
  <h3>What callers ask about</h3>
  {render_topic_breakdown(stats["topic_counts"])}
  <div class="charts-row">
    <div class="chart-col">
      <h3>Calls by hour of day (IST)</h3>
      {render_hour_chart(stats["hour_counts"])}
    </div>
    <div class="chart-col">
      <h3>Calls by day of week</h3>
      {render_day_chart(stats["day_counts"])}
    </div>
  </div>
  <h3>Call log</h3>
  {render_call_table(calls, stats)}
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
