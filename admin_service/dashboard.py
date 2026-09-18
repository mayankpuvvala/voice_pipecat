"""HTML rendering for the dashboard pages. Small composable pieces (one
job each) rather than one long page-builder — see app/admin/routes.py for
the CSS/table styling this borrows.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from admin_service.config import RestaurantConfig
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
.cap-bar-track { background: #e5e7eb; border-radius: 999px; height: 10px; width: 100%; max-width: 320px; overflow: hidden; }
.cap-bar-fill { height: 100%; border-radius: 999px; }
.hour-row { display: flex; align-items: center; gap: 8px; font-size: 0.78rem; margin: 2px 0; }
.hour-label { width: 42px; color: var(--text-muted); }
.hour-bar { background: #6366f1; height: 10px; border-radius: 4px; }
.restaurant-links a { display: inline-block; margin-right: 14px; font-weight: 600; }
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); padding: 12px 16px; margin-bottom: 1.25rem; font-size: 0.85rem; }
.filter-group { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.filter-group label { display: flex; align-items: center; gap: 4px; white-space: nowrap; cursor: pointer; }
.filter-label { font-weight: 600; color: #374151; margin-right: 2px; }
.filters input[type="date"] { border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; font-size: 0.85rem; }
.filters button { border: 1px solid var(--border); background: #f5f6f8; border-radius: 6px; padding: 5px 10px; font-size: 0.8rem; cursor: pointer; }
.filters button:hover { background: #eceef1; }
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
    """Returns (date as dd:mm:yy, time as hh:mm:ss, date as yyyy-mm-dd for
    filtering) in IST, or ("—", "—", "")."""
    if not iso_ts:
        return "—", "—", ""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "—", "—", ""
    local = dt.astimezone(_IST)
    return local.strftime("%d:%m:%y"), local.strftime("%H:%M:%S"), local.strftime("%Y-%m-%d")


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


def _cap_color(pct: float) -> str:
    if pct >= 100:
        return "#991b1b"
    if pct >= 80:
        return "#b45309"
    return "#16a34a"


def render_cap_bar(minutes_used: float, minutes_allowed: int, pct: float) -> str:
    fill_pct = min(pct, 100)
    color = _cap_color(pct)
    return f"""<div>
  <div class="cap-bar-track"><div class="cap-bar-fill" style="width:{fill_pct:.0f}%;background:{color}"></div></div>
  <span class="muted">{minutes_used:.0f} / {minutes_allowed} min used this month ({pct:.0f}%)</span>
</div>"""


def render_stat_tiles(stats: dict[str, Any]) -> str:
    tiles = [
        ("Calls this month", stats["total_calls_this_month"]),
        ("Calls all-time", stats["total_calls_all_time"]),
        ("Avg call length", f"{stats['avg_call_minutes']:.1f} min"),
        ("Shortest / longest", f"{stats['min_call_minutes']:.1f} / {stats['max_call_minutes']:.1f} min"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="value">{v}</div><div class="label">{escape(k)}</div></div>'
        for k, v in tiles
    )
    return f'<div class="tiles">{tiles_html}</div>'


def render_topic_breakdown(topic_counts: dict[str, int]) -> str:
    if not topic_counts:
        return '<p class="muted">No categorized topics yet.</p>'
    badges = "".join(
        f'<span class="badge badge-topic">{escape(name)} · {count}</span>'
        for name, count in topic_counts.items()
    )
    return f"<p>{badges}</p>"


def render_hour_chart(hour_counts: list[int]) -> str:
    peak = max(hour_counts) or 1
    rows = "".join(
        f'<div class="hour-row"><span class="hour-label">{h:02d}:00</span>'
        f'<div class="hour-bar" style="width:{(count / peak) * 200:.0f}px"></div>'
        f'<span class="muted">{count}</span></div>'
        for h, count in enumerate(hour_counts)
        if count > 0
    )
    return rows or '<p class="muted">No call-time data yet.</p>'


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

    return f"""<tr data-date="{iso_date}" data-outcome="{outcome_key}" data-confidence="{confidence_key}">
    <td class="nowrap">{duration_str}<br><span class="muted">{date_str} {time_str}</span></td>
    <td>{escape(call["caller_name"] or "—")}</td>
    <td class="nowrap">{escape(call["caller_phone"] or "—")}</td>
    <td>{topic_html}</td>
    <td>{outcome_badge}</td>
    <td>{recording_html}</td>
    <td><span class="badge {conf_class}">{conf_label}</span></td>
  </tr>"""


_FILTER_BAR = """
<div class="filters">
  <div class="filter-group">
    <label>From <input type="date" id="filter-from"></label>
    <label>To <input type="date" id="filter-to"></label>
  </div>
  <div class="filter-group">
    <span class="filter-label">Outcome</span>
    <label><input type="checkbox" class="f-outcome" value="resolved" checked> Resolved</label>
    <label><input type="checkbox" class="f-outcome" value="followup" checked> Follow-up needed</label>
  </div>
  <div class="filter-group">
    <span class="filter-label">Confidence</span>
    <label><input type="checkbox" class="f-confidence" value="1" checked> High</label>
    <label><input type="checkbox" class="f-confidence" value="2" checked> Medium</label>
    <label><input type="checkbox" class="f-confidence" value="3" checked> Low</label>
    <label><input type="checkbox" class="f-confidence" value="0" checked> Unrated</label>
  </div>
  <button type="button" id="filter-clear">Clear filters</button>
  <span class="muted" id="filter-count"></span>
</div>
"""

_FILTER_SCRIPT = """
<script>
(function() {
  var fromEl = document.getElementById('filter-from');
  var toEl = document.getElementById('filter-to');
  var countEl = document.getElementById('filter-count');
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('#call-table tbody tr[data-date]')
  );

  function checkedValues(selector) {
    return Array.prototype.slice.call(document.querySelectorAll(selector + ':checked'))
      .map(function(el) { return el.value; });
  }

  function applyFilters() {
    var from = fromEl.value;
    var to = toEl.value;
    var outcomes = checkedValues('.f-outcome');
    var confidences = checkedValues('.f-confidence');
    var shown = 0;
    rows.forEach(function(row) {
      var date = row.getAttribute('data-date');
      var ok = true;
      if (date) {
        if (from && date < from) ok = false;
        if (to && date > to) ok = false;
      }
      if (outcomes.indexOf(row.getAttribute('data-outcome')) === -1) ok = false;
      if (confidences.indexOf(row.getAttribute('data-confidence')) === -1) ok = false;
      row.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    countEl.textContent = shown + ' of ' + rows.length + ' shown';
  }

  document.querySelectorAll('.f-outcome, .f-confidence').forEach(function(el) {
    el.addEventListener('change', applyFilters);
  });
  fromEl.addEventListener('change', applyFilters);
  toEl.addEventListener('change', applyFilters);
  document.getElementById('filter-clear').addEventListener('click', function() {
    fromEl.value = '';
    toEl.value = '';
    document.querySelectorAll('.f-outcome, .f-confidence').forEach(function(el) { el.checked = true; });
    applyFilters();
  });

  if (rows.length) applyFilters();
})();
</script>
"""


def render_call_table(calls: list[dict[str, Any]]) -> str:
    columns = ["Call Time", "Caller", "Phone", "Topic", "Outcome", "Recording", "Confidence"]
    header = "".join(f"<th>{c}</th>" for c in columns)
    rows = "".join(render_call_row(c) for c in calls) or (
        f"<tr><td colspan='{len(columns)}'>No calls logged yet.</td></tr>"
    )
    return f"""{_FILTER_BAR}<div class="table-wrap"><table id="call-table">
    <thead><tr>{header}</tr></thead><tbody>{rows}</tbody>
  </table></div>{_FILTER_SCRIPT}"""


def render_restaurant_page(
    cfg: RestaurantConfig, calls: list[dict[str, Any]], stats: dict[str, Any]
) -> str:
    body = f"""
  <h2>{escape(cfg.display_name)}</h2>
  <p class="meta">{len(calls)} call(s) logged, newest first.</p>
  {render_cap_bar(stats["minutes_used_this_month"], stats["minutes_allowed_per_month"], stats["minutes_used_pct"])}
  {render_stat_tiles(stats)}
  <h3>What callers ask about</h3>
  {render_topic_breakdown(stats["topic_counts"])}
  <h3>Calls by hour of day (IST)</h3>
  {render_hour_chart(stats["hour_counts"])}
  <h3>Call log</h3>
  {render_call_table(calls)}
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
      <div class="label">{stats["total_calls_this_month"]} calls this month</div>
      {render_cap_bar(stats["minutes_used_this_month"], stats["minutes_allowed_per_month"], stats["minutes_used_pct"])}
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
