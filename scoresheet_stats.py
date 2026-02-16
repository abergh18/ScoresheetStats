#!/usr/bin/env python3
"""Scoresheet MLB stats tool: CSV CLI export and optional sortable/filterable web UI."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterable, List, Optional

API_BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT_SECONDS = 20
STAT_KEYS = ("AB", "H", "HR", "OPS", "IP", "K")


@dataclass
class Period:
    name: str
    stats_type: str
    season: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch MLB batting/pitching stats + status for Scoresheet player IDs."
    )
    parser.add_argument("--ids", help="Comma-separated MLB player IDs.")
    parser.add_argument("--ids-file", help="Text file with one MLB player ID per line.")
    parser.add_argument(
        "--season",
        type=int,
        default=dt.date.today().year,
        help="Season for full-season window (default: current year).",
    )
    parser.add_argument(
        "--output",
        default="scoresheet_player_stats.csv",
        help="Output CSV path for CLI mode.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run web UI server instead of writing CSV.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for --serve mode.")
    parser.add_argument("--port", type=int, default=8000, help="Port for --serve mode.")
    return parser.parse_args()


def load_player_ids(ids_arg: Optional[str], ids_file: Optional[str]) -> List[int]:
    raw_ids: List[str] = []
    if ids_arg:
        raw_ids.extend(part.strip() for part in ids_arg.split(",") if part.strip())
    if ids_file:
        with open(ids_file, "r", encoding="utf-8") as fh:
            raw_ids.extend(line.strip() for line in fh if line.strip())
    return parse_id_list(raw_ids)


def parse_id_list(raw_ids: List[str]) -> List[int]:
    if not raw_ids:
        raise ValueError("No player IDs provided.")

    seen = set()
    ordered: List[int] = []
    for pid in raw_ids:
        if not str(pid).isdigit():
            raise ValueError(f"Invalid player ID: {pid!r}. IDs must be numeric.")
        value = int(pid)
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def build_periods(season: int) -> List[Period]:
    today = dt.date.today()
    return [
        Period(name="full_season", stats_type="season", season=season),
        Period(name="last_season", stats_type="season", season=season - 1),
        Period(
            name="last_15_days",
            stats_type="byDateRange",
            start_date=(today - dt.timedelta(days=15)).isoformat(),
            end_date=today.isoformat(),
        ),
        Period(
            name="last_30_days",
            stats_type="byDateRange",
            start_date=(today - dt.timedelta(days=30)).isoformat(),
            end_date=today.isoformat(),
        ),
    ]


def get_json(path: str, params: Optional[Dict[str, str]] = None) -> Dict:
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ScoresheetStats/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def fetch_player_profile(player_id: int) -> Dict[str, str]:
    data = get_json(
        f"/people/{player_id}",
        params={"hydrate": "currentTeam,rosterEntries"},
    )
    people = data.get("people", [])
    if not people:
        raise ValueError(f"Player ID {player_id} not found.")

    person = people[0]
    status = "Unknown"
    roster_entries = person.get("rosterEntries", [])
    if roster_entries:
        latest_entry = max(roster_entries, key=lambda entry: entry.get("startDate", ""))
        status = latest_entry.get("status", {}).get("description", status)
    elif isinstance(person.get("active"), bool):
        status = "Active" if person["active"] else "Inactive"

    return {
        "player_id": str(player_id),
        "name": person.get("fullName", "Unknown"),
        "team": person.get("currentTeam", {}).get("name", ""),
        "position": person.get("primaryPosition", {}).get("abbreviation", ""),
        "status": status,
    }


def extract_group_stats(stats_json: Dict, group_name: str) -> Dict[str, str]:
    for block in stats_json.get("stats", []):
        if block.get("group", {}).get("displayName", "").lower() != group_name:
            continue
        splits = block.get("splits", [])
        if not splits:
            return {}
        return splits[0].get("stat", {})
    return {}


def fetch_stats_for_period(player_id: int, period: Period) -> Dict[str, str]:
    params = {"group": "hitting,pitching", "stats": period.stats_type}
    if period.season is not None:
        params["season"] = str(period.season)
    if period.start_date and period.end_date:
        params["startDate"] = period.start_date
        params["endDate"] = period.end_date

    stats_json = get_json(f"/people/{player_id}/stats", params=params)
    hitting = extract_group_stats(stats_json, "hitting")
    pitching = extract_group_stats(stats_json, "pitching")
    return {
        "AB": str(hitting.get("atBats", "")),
        "H": str(hitting.get("hits", "")),
        "HR": str(hitting.get("homeRuns", "")),
        "OPS": str(hitting.get("ops", "")),
        "IP": str(pitching.get("inningsPitched", "")),
        "K": str(pitching.get("strikeOuts", "")),
    }


def fetch_all_player_rows(player_ids: List[int], season: int) -> tuple[List[Dict[str, str]], List[Period], List[str]]:
    periods = build_periods(season)
    player_rows: List[Dict[str, str]] = []
    warnings: List[str] = []
    for player_id in player_ids:
        try:
            profile = fetch_player_profile(player_id)
            for period in periods:
                profile[period.name] = fetch_stats_for_period(player_id, period)
            player_rows.append(profile)
        except Exception as exc:
            warnings.append(f"Could not fetch player {player_id}: {exc}")
    return player_rows, periods, warnings


def flatten_rows(player_rows: Iterable[Dict[str, str]], periods: List[Period]) -> List[Dict[str, str]]:
    output_rows: List[Dict[str, str]] = []
    for row in player_rows:
        flattened: Dict[str, str] = {
            "player_id": row["player_id"],
            "name": row["name"],
            "team": row["team"],
            "position": row["position"],
            "status": row["status"],
        }
        for period in periods:
            prefix = period.name
            period_stats = row.get(prefix, {})
            for stat_key in STAT_KEYS:
                flattened[f"{prefix}_{stat_key}"] = period_stats.get(stat_key, "")
        output_rows.append(flattened)
    return output_rows


def write_csv(rows: List[Dict[str, str]], periods: List[Period], output_path: str) -> None:
    fieldnames = ["player_id", "name", "team", "position", "status"]
    for period in periods:
        for stat_key in STAT_KEYS:
            fieldnames.append(f"{period.name}_{stat_key}")

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def html_page() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset='utf-8' />
  <title>Scoresheet Stats UI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    textarea { width: 420px; height: 100px; }
    table { border-collapse: collapse; margin-top: 15px; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 6px; text-align: right; }
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3), th:nth-child(4), td:nth-child(4), th:nth-child(5), td:nth-child(5) { text-align: left; }
    th.sortable { cursor: pointer; background: #f7f7f7; }
    .row { display: flex; gap: 20px; align-items: end; }
    .small { color: #555; font-size: 12px; }
    #warnings { color: #8a4b00; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h2>Scoresheet Player Stats</h2>
  <div class='row'>
    <div>
      <label>Player IDs (comma/newline separated)</label><br>
      <textarea id='ids'></textarea>
    </div>
    <div>
      <label>Season</label><br>
      <input id='season' type='number' value='' />
      <br><br>
      <button id='load'>Fetch Stats</button>
      <div class='small'>Tip: paste all 40 MLB IDs.</div>
    </div>
    <div>
      <label>Filter Position</label><br>
      <select id='positionFilter'><option value=''>All</option></select>
    </div>
  </div>
  <div id='warnings'></div>
  <div id='tableWrap'></div>

<script>
let rows = [];
let sortKey = '';
let sortDesc = true;

const defaultSeason = new Date().getFullYear();
document.getElementById('season').value = defaultSeason;

function toNumberMaybe(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function sortRows(data) {
  if (!sortKey) return data;
  return [...data].sort((a,b) => {
    const av = a[sortKey] ?? '';
    const bv = b[sortKey] ?? '';
    const an = toNumberMaybe(av);
    const bn = toNumberMaybe(bv);
    let cmp = 0;
    if (an !== null && bn !== null) cmp = an - bn;
    else cmp = String(av).localeCompare(String(bv));
    return sortDesc ? -cmp : cmp;
  });
}

function render() {
  const pos = document.getElementById('positionFilter').value;
  let filtered = rows;
  if (pos) filtered = rows.filter(r => r.position === pos);
  filtered = sortRows(filtered);

  if (!filtered.length) {
    document.getElementById('tableWrap').innerHTML = '<p>No rows to display.</p>';
    return;
  }

  const columns = Object.keys(filtered[0]);
  const thead = '<tr>' + columns.map(c => `<th class="sortable" data-col="${c}">${c}</th>`).join('') + '</tr>';
  const tbody = filtered.map(r => '<tr>' + columns.map(c => `<td>${r[c] ?? ''}</td>`).join('') + '</tr>').join('');
  document.getElementById('tableWrap').innerHTML = `<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;

  document.querySelectorAll('th.sortable').forEach(el => {
    el.onclick = () => {
      const key = el.dataset.col;
      if (sortKey === key) sortDesc = !sortDesc;
      else { sortKey = key; sortDesc = true; }
      render();
    };
  });
}

function updatePositionFilter() {
  const select = document.getElementById('positionFilter');
  const vals = [...new Set(rows.map(r => r.position).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All</option>' + vals.map(v => `<option>${v}</option>`).join('');
}

document.getElementById('positionFilter').addEventListener('change', render);

document.getElementById('load').addEventListener('click', async () => {
  const ids = document.getElementById('ids').value;
  const season = Number(document.getElementById('season').value || defaultSeason);
  document.getElementById('warnings').textContent = 'Loading...';
  try {
    const res = await fetch('/api/fetch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids, season})
    });
    const payload = await res.json();
    rows = payload.rows || [];
    updatePositionFilter();
    render();
    document.getElementById('warnings').textContent = (payload.warnings || []).join('\n');
  } catch (e) {
    document.getElementById('warnings').textContent = 'Request failed: ' + e;
  }
});
</script>
</body>
</html>"""


class ScoresheetHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: Dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = html_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/fetch":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            ids_blob = payload.get("ids", "")
            season = int(payload.get("season", dt.date.today().year))
            split_ids = [p.strip() for p in ids_blob.replace(",", "\n").splitlines() if p.strip()]
            player_ids = parse_id_list(split_ids)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return

        rows_raw, periods, warnings = fetch_all_player_rows(player_ids, season)
        rows = flatten_rows(rows_raw, periods)
        self._send_json(200, {"rows": rows, "warnings": warnings})


def run_server(host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), ScoresheetHandler)
    print(f"Serving UI at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()
    return 0


def run_cli(args: argparse.Namespace) -> int:
    try:
        player_ids = load_player_ids(args.ids, args.ids_file)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rows_raw, periods, warnings = fetch_all_player_rows(player_ids, args.season)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if not rows_raw:
        print("No player data was fetched.", file=sys.stderr)
        return 1

    flat_rows = flatten_rows(rows_raw, periods)
    write_csv(flat_rows, periods, args.output)
    print(f"Wrote {len(flat_rows)} rows to {args.output}")
    return 0


def main() -> int:
    args = parse_args()
    if args.serve:
        return run_server(args.host, args.port)
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
