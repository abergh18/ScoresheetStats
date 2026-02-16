# Scoresheet Stats Dashboard

A Streamlit app that takes MLB player IDs and shows sortable hitter/pitcher stats for your Scoresheet fantasy team.

## Features

- Enter your MLB player IDs (comma-separated or one per line).
- View **hitters and pitchers separately**.
- Filter by:
  - Position
  - Player status (active, minors, IL, etc. when available from MLB API)
- Select stat window:
  - Full Season
  - Last Season
  - Last 15 Days
  - Last 30 Days
- Click any table header to sort that stat.

## Stats included

### Batting
- AB
- AVG
- OBP
- SLG
- OPS
- HR
- SB
- SB%

### Pitching
- IP
- K
- ERA
- Batters Faced
- WHIP
- AVG
- K/9
- BB/9
- K/BB

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL printed by Streamlit (usually `http://localhost:8501`).

## Data source

- MLB Stats API (`https://statsapi.mlb.com`)
