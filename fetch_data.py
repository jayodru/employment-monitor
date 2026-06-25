#!/usr/bin/env python3
"""
Employment Landscape Monitor — weekly data fetch.

Pulls three layers of US employment data and writes a single data.json
that the dashboard (index.html) reads. Designed to run unattended on a
weekly schedule (e.g. GitHub Actions).

Sources (all free, public domain):
  - BLS API v2          : unemployment rate, sector payrolls       (api.bls.gov)
  - FRED (St. Louis Fed): weekly initial & continued jobless claims (fred.stlouisfed.org)
  - Cleveland Fed WARN  : state-level WARN layoff panel             (openICPSR mirror)

A free FRED API key is recommended (set FRED_API_KEY). A free BLS key
(set BLS_API_KEY) raises rate limits but is optional. Everything degrades
gracefully: if one source fails, the others still publish and the
dashboard flags the gap rather than breaking.
"""

import os
import json
import datetime as dt
import urllib.request
import urllib.error

OUT_PATH = os.path.join(os.path.dirname(__file__), "data", "data.json")

BLS_API_KEY = os.environ.get("BLS_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

UA = {"User-Agent": "employment-monitor/1.0 (personal dashboard)"}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _get_json(url, data=None, headers=None):
    hdrs = dict(UA)
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _safe(label, fn):
    """Run a fetcher, never let one failure sink the whole run."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        print(f"  ! {label} failed: {e}")
        return None, str(e)


# ----------------------------------------------------------------------
# 1. BLS — unemployment rate + sector payroll employment
# ----------------------------------------------------------------------
# Series IDs:
#   LNS14000000  national unemployment rate (seasonally adj)
#   Sector payrolls (CES, thousands, SA) — total nonfarm and major sectors
BLS_SERIES = {
    "LNS14000000": "Unemployment rate",
    "CES0000000001": "Total nonfarm",
    "CES1000000001": "Mining & logging",
    "CES2000000001": "Construction",
    "CES3000000001": "Manufacturing",
    "CES4000000001": "Trade, transport & utilities",
    "CES5000000001": "Information",
    "CES5500000001": "Financial activities",
    "CES6000000001": "Professional & business",
    "CES6500000001": "Education & health",
    "CES7000000001": "Leisure & hospitality",
    "CES9000000001": "Government",
}


def fetch_bls():
    end = dt.date.today().year
    start = end - 2
    payload = {
        "seriesid": list(BLS_SERIES.keys()),
        "startyear": str(start),
        "endyear": str(end),
    }
    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY
    body = json.dumps(payload).encode("utf-8")
    resp = _get_json(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    out = {}
    for s in resp.get("Results", {}).get("series", []):
        sid = s["seriesID"]
        pts = []
        for d in s["data"]:
            if d["period"].startswith("M"):
                pts.append({
                    "date": f'{d["year"]}-{d["period"][1:]}',
                    "value": float(d["value"]),
                })
        pts.sort(key=lambda x: x["date"])
        out[BLS_SERIES[sid]] = pts
    return out


# ----------------------------------------------------------------------
# 2. FRED — weekly jobless claims
# ----------------------------------------------------------------------
FRED_SERIES = {
    "ICSA": "Initial claims",
    "CCSA": "Continued claims",
    "IC4WSA": "Initial claims (4-wk avg)",
}


def fetch_fred():
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY not set")
    out = {}
    start = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    for sid, label in FRED_SERIES.items():
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={sid}&api_key={FRED_API_KEY}&file_type=json"
            f"&observation_start={start}"
        )
        resp = _get_json(url)
        pts = [
            {"date": o["date"], "value": float(o["value"])}
            for o in resp.get("observations", [])
            if o["value"] not in (".", "")
        ]
        out[label] = pts
    return out


# ----------------------------------------------------------------------
# 3. WARN — Cleveland Fed state panel
# ----------------------------------------------------------------------
# The Cleveland Fed publishes a maintained national WARN panel (workers
# affected by state and month) plus a national "WARN factor". The stable
# public mirror is the openICPSR project. Because that download is a zip
# behind a landing page, the most robust weekly path is the maintained
# CSV the Fed exposes. If unreachable, we fall back to the last good copy
# already committed in data/warn_last_good.json so the dashboard stays up.
WARN_CSV = (
    "https://raw.githubusercontent.com/"
    "plunsford/warn-layoffs/main/WARNData_NSA_latest.csv"
)


def fetch_warn():
    # Primary: maintained CSV (state x month, workers affected)
    req = urllib.request.Request(WARN_CSV, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    header = [h.strip() for h in lines[0].split(",")]
    rows = []
    for ln in lines[1:]:
        cells = ln.split(",")
        rec = dict(zip(header, [c.strip() for c in cells]))
        rows.append(rec)
    return {"columns": header, "rows": rows}


# ----------------------------------------------------------------------
# driving-factors narrative (derived from the data movements)
# ----------------------------------------------------------------------
def derive_narrative(bls, fred, warn):
    notes = []
    if fred and fred.get("Initial claims"):
        ic = fred["Initial claims"]
        if len(ic) >= 5:
            latest = ic[-1]["value"]
            prior = ic[-2]["value"]
            mo_ago = ic[-5]["value"]
            wk = "rose" if latest > prior else "fell"
            mo = "up" if latest > mo_ago else "down"
            notes.append(
                f"Initial jobless claims {wk} week-over-week to "
                f"{latest:,.0f}, and are {mo} versus a month ago."
            )
    if bls and bls.get("Unemployment rate"):
        ur = bls["Unemployment rate"]
        if ur:
            notes.append(
                f"National unemployment rate stands at {ur[-1]['value']:.1f}% "
                f"as of {ur[-1]['date']}."
            )
    if bls:
        # sector momentum: 3-month change in payrolls
        movers = []
        for label, pts in bls.items():
            if label in ("Unemployment rate", "Total nonfarm"):
                continue
            if len(pts) >= 4:
                chg = pts[-1]["value"] - pts[-4]["value"]
                movers.append((label, chg))
        movers.sort(key=lambda x: x[1])
        if movers:
            worst = movers[0]
            best = movers[-1]
            notes.append(
                f"Over the last 3 months, {best[0]} added the most jobs "
                f"({best[1]:+,.0f}k) while {worst[0]} contracted the most "
                f"({worst[1]:+,.0f}k)."
            )
    if not notes:
        notes.append("Data refresh completed; awaiting next source update.")
    return notes


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main():
    print("Fetching employment data...")
    bls, bls_err = _safe("BLS", fetch_bls)
    fred, fred_err = _safe("FRED", fetch_fred)
    warn, warn_err = _safe("WARN", fetch_warn)

    # WARN fallback to last good copy
    warn_lastgood = os.path.join(os.path.dirname(__file__), "data", "warn_last_good.json")
    if warn is None and os.path.exists(warn_lastgood):
        with open(warn_lastgood) as f:
            warn = json.load(f)
        warn_err = (warn_err or "") + " (using last good copy)"
    elif warn is not None:
        with open(warn_lastgood, "w") as f:
            json.dump(warn, f)

    out = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "sources": {
            "bls": {"ok": bls is not None, "error": bls_err},
            "fred": {"ok": fred is not None, "error": fred_err},
            "warn": {"ok": warn is not None, "error": warn_err},
        },
        "bls": bls or {},
        "fred": fred or {},
        "warn": warn or {},
        "narrative": derive_narrative(bls, fred, warn),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f)
    print(f"Wrote {OUT_PATH}")
    print(f"  BLS ok={bls is not None}  FRED ok={fred is not None}  WARN ok={warn is not None}")


if __name__ == "__main__":
    main()
