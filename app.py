from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
import streamlit as st

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
PLAYER_IDS_QUERY_PARAM = "player_ids"
APP_STATE_PATH = Path.home() / ".scoresheet_stats_state.json"

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


@st.cache_data(show_spinner=False)
def load_saved_state() -> dict[str, str]:
    if not APP_STATE_PATH.exists():
        return {}

    try:
        saved_state = json.loads(APP_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(saved_state, dict):
        return {}

    return {
        "player_ids": str(saved_state.get("player_ids", "")),
        "period_name": str(saved_state.get("period_name", "Full Season")),
    }


def get_saved_player_ids() -> str:
    query_params = getattr(st, "query_params", None)
    if query_params is not None:
        saved_ids = query_params.get(PLAYER_IDS_QUERY_PARAM, "")
        if isinstance(saved_ids, list):
            query_saved_ids = saved_ids[0] if saved_ids else ""
        else:
            query_saved_ids = str(saved_ids)
        if query_saved_ids:
            return query_saved_ids

    getter = getattr(st, "experimental_get_query_params", None)
    if getter is not None:
        saved_ids = getter().get(PLAYER_IDS_QUERY_PARAM, [""])
        if saved_ids and saved_ids[0]:
            return saved_ids[0]

    return load_saved_state().get("player_ids", "")



def save_app_state(raw_ids: str, period_name: str) -> None:
    compact_ids = ",".join(x.strip() for x in raw_ids.replace("\n", ",").split(",") if x.strip())
    state_payload = {
        "player_ids": compact_ids,
        "period_name": period_name,
    }
    try:
        APP_STATE_PATH.write_text(json.dumps(state_payload), encoding="utf-8")
        load_saved_state.clear()
    except OSError:
        pass

    query_params = getattr(st, "query_params", None)
    if query_params is not None:
        if compact_ids:
            query_params[PLAYER_IDS_QUERY_PARAM] = compact_ids
        elif PLAYER_IDS_QUERY_PARAM in query_params:
            del query_params[PLAYER_IDS_QUERY_PARAM]
        return

    setter = getattr(st, "experimental_set_query_params", None)
    if setter is not None:
        if compact_ids:
            setter(**{PLAYER_IDS_QUERY_PARAM: compact_ids})
        else:
            setter()



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

        bat_side = (person.get("batSide") or {}).get("code", "")
        pitch_hand = (person.get("pitchHand") or {}).get("code", "")
        position_type = (person.get("primaryPosition") or {}).get("type", "Unknown")
        handedness = pitch_hand if position_type == "Pitcher" else bat_side

        rows.append(
            {
                "player_id": int(person["id"]),
                "Name": person.get("fullName", "Unknown"),
                "Position": (person.get("primaryPosition") or {}).get("abbreviation", "N/A"),
                "Position Type": position_type,
                "Bats/Throws": handedness or "N/A",
                "Team": ((active_entry or {}).get("team") or {}).get("abbreviation", "N/A"),
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
        player_id = person_stats.get("id", [])

        for group_stats in person_stats.get("stats", []):
            for split in group_stats.get("splits", []) or []:
                split["player_id"] = player_id
                splits.append(split)

    rows: list[dict[str, Any]] = []
    for split in splits:
        stat = split.get("stat") or {}
        team = split.get("team") or {}
        row = {
            "player_id": split["player_id"],
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



def coerce_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    converted = df.copy()
    for column in columns:
        if column not in converted.columns:
            continue
        numeric_values = pd.to_numeric(converted[column], errors="coerce")
        if numeric_values.notna().any():
            converted[column] = numeric_values
    return converted



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

    out = merged[["player_id", "Name", "Position", "Bats/Throws", "Team", "Status"]].copy()
    for label, key in stat_map.items():
        out[label] = merged[key] if key in merged.columns else ""

    return coerce_numeric_columns(out, list(stat_map.keys()))



def load_stats(
    player_ids: list[int],
    selected_period: str,
    selected_period_name: str,
    season_year: int,
) -> None:
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
            "hitting_min",
            "hitting_max",
            "pitching_positions",
            "pitching_statuses",
            "pitching_min",
            "pitching_max",
        ]
    )



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



def render_numeric_filters(df: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    numeric_columns = [
        column
        for column in df.columns
        if column not in {"player_id"} and pd.api.types.is_numeric_dtype(df[column])
    ]
    if not numeric_columns:
        return df

    active_filters = 0
    for column in numeric_columns:
        minimum_value = st.session_state.get(f"{key_prefix}_min__{column}", "")
        maximum_value = st.session_state.get(f"{key_prefix}_max__{column}", "")
        if str(minimum_value).strip() or str(maximum_value).strip():
            active_filters += 1

    filtered_df = df.copy()
    with st.popover(f"Stat filters ({active_filters})", use_container_width=True):
        st.caption("Set a minimum and/or maximum for any numeric stat column.")
        for column in numeric_columns:
            min_key = f"{key_prefix}_min__{column}"
            max_key = f"{key_prefix}_max__{column}"
            label_col, min_col, max_col = st.columns([1.2, 1, 1])
            label_col.markdown(f"**{column}**")
            min_col.text_input("Min", key=min_key, label_visibility="collapsed", placeholder=">=")
            max_col.text_input("Max", key=max_key, label_visibility="collapsed", placeholder="<=")

        if st.button("Clear stat filters", key=f"{key_prefix}_clear_numeric", use_container_width=True):
            for column in numeric_columns:
                st.session_state[f"{key_prefix}_min__{column}"] = ""
                st.session_state[f"{key_prefix}_max__{column}"] = ""
            st.rerun()

    for column in numeric_columns:
        min_value = str(st.session_state.get(f"{key_prefix}_min__{column}", "")).strip()
        max_value = str(st.session_state.get(f"{key_prefix}_max__{column}", "")).strip()

        if min_value:
            try:
                filtered_df = filtered_df[filtered_df[column] >= float(min_value)]
            except ValueError:
                st.warning(f"Ignoring invalid minimum for {column}: {min_value}")
        if max_value:
            try:
                filtered_df = filtered_df[filtered_df[column] <= float(max_value)]
            except ValueError:
                st.warning(f"Ignoring invalid maximum for {column}: {max_value}")

    return filtered_df



def render_filters_and_table(df: pd.DataFrame, key_prefix: str) -> None:
    if df.empty:
        st.info("No player data found for this selection.")
        return

    positions = sorted(x for x in df["Position"].dropna().unique().tolist())
    statuses = sorted(x for x in df["Status"].dropna().unique().tolist())

    c1, c2, c3 = st.columns(3)
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
    with c3:
        filtered = render_numeric_filters(filtered, key_prefix)

    st.dataframe(filtered, hide_index=True, use_container_width=True)



def main() -> None:
    st.set_page_config(page_title="Scoresheet Stats Dashboard", layout="wide")
    st.title("Scoresheet Fantasy Baseball Stats")

    st.markdown(
        "Enter your MLB player IDs to get hitting and pitching stats, status, and sortable filters."
    )

    saved_state = load_saved_state()
    default_ids = st.session_state.get("saved_player_ids", get_saved_player_ids())
    raw_ids = st.text_area(
        "MLB player IDs (comma-separated or one per line)",
        value=default_ids,
        height=120,
        placeholder="545361, 592450, 621043, ...",
    )
    st.session_state["saved_player_ids"] = raw_ids

    period_options = {
        "Full Season": "full",
        "Last Season": "last_season",
        "Last 15 Days": "last_15",
        "Last 30 Days": "last_30",
    }
    default_period_name = saved_state.get("period_name", "Full Season")
    default_period_index = (
        list(period_options.keys()).index(default_period_name)
        if default_period_name in period_options
        else 0
    )
    selected_period_name = st.selectbox(
        "Stat time window",
        options=list(period_options.keys()),
        index=default_period_index,
    )
    selected_period = period_options[selected_period_name]

    today = dt.date.today()
    season_year = today.year

    should_load_stats = st.button("Load Stats", type="primary")

    if should_load_stats:
        try:
            player_ids = parse_player_ids(raw_ids)
        except ValueError as exc:
            st.error(str(exc))
            return

        if not player_ids:
            st.warning("Please enter at least one MLB player ID.")
            return

        save_app_state(raw_ids, selected_period_name)
        st.session_state["saved_player_ids"] = raw_ids

        with st.spinner("Fetching player metadata and stats from MLB..."):
            load_stats(player_ids, selected_period, selected_period_name, season_year)

    elif raw_ids and "loaded_stats" not in st.session_state:
        try:
            player_ids = parse_player_ids(raw_ids)
        except ValueError:
            player_ids = []

        if player_ids:
            with st.spinner("Loading saved player stats..."):
                load_stats(player_ids, selected_period, selected_period_name, season_year)

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
