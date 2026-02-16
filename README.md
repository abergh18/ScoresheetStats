# ScoresheetStats

Python app for Scoresheet fantasy leagues to fetch MLB stats for your player IDs.

## Features
- Pulls roster status + position + team + name for each MLB ID.
- Pulls core stats (`AB`, `H`, `HR`, `OPS`, `IP`, `K`) for:
  - full season
  - last season
  - last 15 days
  - last 30 days
- Two ways to use it:
  - **CLI CSV export** (good for spreadsheets)
  - **Web UI** with sortable columns and position filter

## Run CLI export
```bash
python3 scoresheet_stats.py --ids 592450,660271,605141 --output my_team_stats.csv
```

Or with an IDs file:
```bash
python3 scoresheet_stats.py --ids-file player_ids.txt --output my_team_stats.csv
```

## Run the UI
```bash
python3 scoresheet_stats.py --serve --host 127.0.0.1 --port 8000
```

Then open: `http://127.0.0.1:8000`

### UI behavior
- Paste IDs (comma-separated or one-per-line).
- Click **Fetch Stats**.
- Click any column header to sort (highest/lowest toggles on repeated clicks).
- Use **Filter Position** to show only `SP`, `RP`, `C`, `1B`, etc.

## Notes
- Status/transactions depend on current MLB API data timing.
- Some players only have hitting or pitching stats.
- Uses only Python standard library (no third-party dependencies).
