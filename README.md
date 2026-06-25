# US Employment Landscape Monitor

A self-updating dashboard tracking US layoffs (WARN), weekly jobless claims, and
sector employment trends. See **SETUP_GUIDE.md** for step-by-step, no-code setup.

## Files
- `index.html` ............ the dashboard (open in any browser / hosted via GitHub Pages)
- `fetch_data.py` ......... weekly data fetcher (BLS + FRED + Cleveland Fed WARN)
- `data/data.json` ........ the data the dashboard reads (refreshed weekly)
- `.github/workflows/` .... the weekly automation
- `SETUP_GUIDE.md` ........ plain-English setup instructions

## Data sources (all free, public domain)
- BLS API v2 — unemployment rate, sector payrolls
- FRED — weekly initial & continued jobless claims
- Cleveland Fed WARN panel — state-level layoff notices

Informational only; not investment or employment advice.
