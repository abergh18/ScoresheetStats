from __future__ import annotations

import datetime as dt
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
import streamlit as st

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

HITTING_STAT_MAP = {
    "AB": "atBats",
    "AVG": "avg",
    "OBP": "obp",
    "SLG": "slg",
    "OPS": "ops",
    "HR": "homeRuns",
    "SB": "stolenBases",
    "SB%": "stolenBasePercentage",
}

PITCHING_STAT_MAP = {
    "IP": "inningsPitched",
    "K": "strikeOuts",
    "ERA": "era",
    "Batters Faced": "battersFaced",
    "WHIP": "whip",
    "AVG": "avg",
    "K/9": "strikeoutsPer9Inn",
    "BB/9": "walksPer9Inn",
    "K/BB": "strikeoutWalkRatio",
}


def _api_get(params: dict[str, Any]) -> dict[str, Any]:
    query = urlencode(params)
    url = f"{MLB_API_BASE}/people?{query}"
    with urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_player_ids(raw_ids: str) -> list[int]:
    chunks = [x.strip() for x in raw_ids.replace("\n", ",").split(",")]
    parsed: list[int] = []
    for chunk in chunks:
        if not chunk:
            continue
        if not chunk.isdigit():
            raise ValueError(f"Invalid player ID: {chunk}")
        parsed.append(int(chunk))
    return sorted(set(parsed))


@st.cache_data(show_spinner=False)
def fetch_player_metadata(player_ids: tuple[int, ...]) -> pd.DataFrame:
    if not player_ids:
        return pd.DataFrame()

    data = _api_get(
        {
            "personIds": ",".join(str(x) for x in player_ids),
            "hydrate": "currentTeam,rosterEntries",
            "appContext": "majorLeague",
        },
    )

    rows: list[dict[str, Any]] = []
    for person in data.get("people", []):
        roster_entries = person.get("rosterEntries") or []
        active_entry = next((x for x in roster_entries if not x.get("endDate")), None)
        first_entry = active_entry or (roster_entries[0] if roster_entries else {})
        status = (first_entry.get("status") or {}).get("description")
        if not status:
            status = "Active" if person.get("active") else "Unknown"

        rows.append(
            {
                "player_id": int(person["id"]),
                "Name": person.get("fullName", "Unknown"),
                "Position": (person.get("primaryPosition") or {}).get("abbreviation", "N/A"),
                "Position Type": (person.get("primaryPosition") or {}).get("type", "Unknown"),
                "Team": (person.get("currentTeam") or {}).get("name", "N/A"),
                "Status": status,
            }
        )

    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def fetch_stats(
    player_ids: tuple[int, ...],
    group: str,
    period_key: str,
    season_year: int,
) -> pd.DataFrame:
    if not player_ids:
        return pd.DataFrame()

    today = dt.date.today()
    if period_key == "full":
        hydrate_stats = f"stats(group=[{group}],type=[season],season={season_year})"
    elif period_key == "last_season":
        hydrate_stats = f"stats(group=[{group}],type=[season],season={season_year - 1})"
    elif period_key == "last_15":
        start = (today - dt.timedelta(days=15)).isoformat()
        end = today.isoformat()
        hydrate_stats = (
            f"stats(group=[{group}],type=[byDateRange],startDate={start},endDate={end})"
        )
    elif period_key == "last_30":
        start = (today - dt.timedelta(days=30)).isoformat()
        end = today.isoformat()
        hydrate_stats = (
            f"stats(group=[{group}],type=[byDateRange],startDate={start},endDate={end})"
        )
    else:
        raise ValueError(f"Unsupported period: {period_key}")

    params: dict[str, Any] = {
        "personIds": ",".join(str(x) for x in player_ids),
        "hydrate": hydrate_stats,
    }

    data = _api_get(params)
    splits = []
    for person_stats in data.get("people", []) or []:
        for group_stats in person_stats.get("stats", []):
            splits.extend(group_stats.get("splits", []))

    rows: list[dict[str, Any]] = []
    for split in splits:
        player = split.get("player") or {}
        stat = split.get("stat") or {}
        team = split.get("team") or {}
        row = {
            "player_id": int(player.get("id", 0)),
            "stat_team_name": team.get("name", ""),
        }
        row.update(stat)
        rows.append(row)

    stat_df = pd.DataFrame(rows)
    if stat_df.empty:
        return stat_df

    stat_df["is_full_season_total"] = stat_df["stat_team_name"].eq("")
    preferred_rows = (
        stat_df.sort_values(
            by=["player_id", "is_full_season_total"],
            ascending=[True, False],
            kind="stable",
        )
        .drop_duplicates(subset=["player_id"], keep="first")
        .drop(columns=["stat_team_name", "is_full_season_total"], errors="ignore")
    )
    return preferred_rows


def build_stats_table(
    metadata: pd.DataFrame,
    stat_df: pd.DataFrame,
    stat_map: dict[str, str],
) -> pd.DataFrame:
    if metadata.empty:
        return pd.DataFrame()

    merged = metadata.copy()
    if not stat_df.empty:
        merged = merged.merge(stat_df, on="player_id", how="left")

    out = merged[["Name", "Position", "Team", "Status"]].copy()
    for label, key in stat_map.items():
        out[label] = merged[key] if key in merged.columns else ""

    return out


def set_filter_values(state_key: str, options: list[str], selected: bool) -> None:
    for option in options:
        st.session_state[f"{state_key}__{option}"] = selected


def reset_filter_state(filter_prefixes: list[str]) -> None:
    keys_to_remove = [
        key
        for key in st.session_state.keys()
        if any(key == prefix or key.startswith(f"{prefix}__") for prefix in filter_prefixes)
    ]
    for key in keys_to_remove:
        st.session_state.pop(key, None)


def render_checkbox_filter(label: str, options: list[str], state_key: str) -> list[str]:
    if not options:
        return []

    for option in options:
        widget_key = f"{state_key}__{option}"
        if widget_key not in st.session_state:
            st.session_state[widget_key] = True

    selected_count = sum(
        1 for option in options if st.session_state.get(f"{state_key}__{option}", False)
    )

    with st.popover(f"{label} ({selected_count}/{len(options)})", use_container_width=True):
        control_cols = st.columns(2)
        control_cols[0].button(
            "Select all",
            key=f"{state_key}_all",
            use_container_width=True,
            on_click=set_filter_values,
            args=(state_key, options, True),
        )
        control_cols[1].button(
            "Clear all",
            key=f"{state_key}_none",
            use_container_width=True,
            on_click=set_filter_values,
            args=(state_key, options, False),
        )

        for option in options:
            st.checkbox(option, key=f"{state_key}__{option}")

    selected_values = [
        option for option in options if st.session_state.get(f"{state_key}__{option}", False)
    ]
    st.session_state[state_key] = selected_values
    return selected_values


def render_filters_and_table(df: pd.DataFrame, key_prefix: str) -> None:
    if df.empty:
        st.info("No player data found for this selection.")
        return

    positions = sorted(x for x in df["Position"].dropna().unique().tolist())
    statuses = sorted(x for x in df["Status"].dropna().unique().tolist())

    c1, c2 = st.columns(2)
    with c1:
        selected_positions = render_checkbox_filter(
            "Position",
            positions,
            f"{key_prefix}_positions",
        )
    with c2:
        selected_statuses = render_checkbox_filter(
            "Status",
            statuses,
            f"{key_prefix}_statuses",
        )

    filtered = df[
        df["Position"].isin(selected_positions) & df["Status"].isin(selected_statuses)
    ]

    st.caption(
        "Tip: use the filter popovers to check or uncheck values, then click any column header to sort."
    )
    st.dataframe(filtered, hide_index=True, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Scoresheet Stats Dashboard", layout="wide")
    st.title("Scoresheet Fantasy Baseball Stats")

    st.markdown(
        "Enter your MLB player IDs to get hitting and pitching stats, status, and sortable filters."
    )

    default_ids = ""
    raw_ids = st.text_area(
        "MLB player IDs (comma-separated or one per line)",
        value=default_ids,
        height=120,
        placeholder="545361, 592450, 621043, ...",
    )

    period_options = {
        "Full Season": "full",
        "Last Season": "last_season",
        "Last 15 Days": "last_15",
        "Last 30 Days": "last_30",
    }
    selected_period_name = st.selectbox("Stat time window", options=list(period_options.keys()))
    selected_period = period_options[selected_period_name]

    today = dt.date.today()
    season_year = today.year

    if st.button("Load Stats", type="primary"):
        try:
            player_ids = parse_player_ids(raw_ids)
        except ValueError as exc:
            st.error(str(exc))
            return

        if not player_ids:
            st.warning("Please enter at least one MLB player ID.")
            return

        with st.spinner("Fetching player metadata and stats from MLB..."):
            player_tuple = tuple(player_ids)
            metadata = fetch_player_metadata(player_tuple)
            hitting_stats = fetch_stats(player_tuple, "hitting", selected_period, season_year)
            pitching_stats = fetch_stats(player_tuple, "pitching", selected_period, season_year)

        hitting_table = build_stats_table(metadata, hitting_stats, HITTING_STAT_MAP)
        pitching_table = build_stats_table(metadata, pitching_stats, PITCHING_STAT_MAP)

        hitter_positions = metadata[metadata["Position Type"] != "Pitcher"]["player_id"].tolist()
        pitcher_positions = metadata[metadata["Position Type"] == "Pitcher"]["player_id"].tolist()

        if hitter_positions:
            hitting_table = hitting_table[
                hitting_table["Name"].isin(
                    metadata[metadata["player_id"].isin(hitter_positions)]["Name"]
                )
            ]
        if pitcher_positions:
            pitching_table = pitching_table[
                pitching_table["Name"].isin(
                    metadata[metadata["player_id"].isin(pitcher_positions)]["Name"]
                )
            ]

        st.session_state["loaded_stats"] = {
            "period_name": selected_period_name,
            "hitting_table": hitting_table,
            "pitching_table": pitching_table,
        }

        reset_filter_state(
            [
                "hitting_positions",
                "hitting_statuses",
                "pitching_positions",
                "pitching_statuses",
            ]
        )

    loaded_stats = st.session_state.get("loaded_stats")
    if loaded_stats:
        t1, t2 = st.tabs(["Hitters", "Pitchers"])

        with t1:
            st.subheader(f"Hitting stats: {loaded_stats['period_name']}")
            render_filters_and_table(loaded_stats["hitting_table"], "hitting")

        with t2:
            st.subheader(f"Pitching stats: {loaded_stats['period_name']}")
            render_filters_and_table(loaded_stats["pitching_table"], "pitching")


if __name__ == "__main__":
    main()
