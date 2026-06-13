# Market Intelligence Platform
**Data Analytics Engineering Lead — Case Study Submission**
Really Big Bank | Post-Trade Market Services

---

## Overview
This solution builds an end-to-end market intelligence pipeline that ingests 
ASX 200 daily price data (Yahoo Finance) and the RBA Interbank Overnight Cash Rate, 
calculates three operational metrics, and serves a single management dashboard 
answering: *"How has market activity trended over the past 90 days, and is there 
anything we should be watching?"*

**Why this data combination?** The RBA Cash Rate is the most operationally 
relevant macro signal for an Australian post-trade team — it directly 
influences settlement costs and funding curves. Pairing it with ASX 200 
volume/price data gives leadership a view of whether market activity is 
expanding or compressing relative to the rate environment.

---

## Setup & Run Instructions

**Prerequisites:** Python 3.10+, pip

```bash
# 1. Clone the repository
git clone https://github.com/[username]/market-intelligence-platform.git
cd market-intelligence-platform

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run ingestion (pulls last 90 days)
python -m pipeline.ingest --days 90

# 5. Run transformation and metric calculation
python -m pipeline.transform

# 6. Launch the dashboard
python dashboard/dashboard.py
# Open http://127.0.0.1:8050 in your browser

# 7. (Optional) Run tests
pytest tests/
```

No API keys required. Yahoo Finance and RBA data are accessed via free 
public endpoints. If Yahoo Finance rate-limits the free tier, re-run 
`ingest.py` after a 60-second pause.

---

## Metric Definitions
*These definitions were written before any transformation code was generated.*

**1. 20-Day Rolling Average Price**
The average of the ASX 200 closing price over the past 20 trading days. 
Smooths daily noise to show the underlying trend direction. If current 
price sits above the rolling average, the market is trending upward; 
below indicates a downward trend.

**2. 14-Day Annualised Volatility**
The standard deviation of ASX 200 daily log returns over the past 14 
trading days, scaled to an annualised figure (×√252). Measures how much 
price uncertainty exists right now — high volatility correlates with 
elevated settlement and operational risk.

**3. Month-on-Month RBA Rate Change (basis points)**
The difference in the RBA Cash Rate Target between the current and prior 
month, expressed in basis points. Flags whether the rate environment is 
tightening, easing, or on hold.

---

## Architecture & Key Design Decisions

- **Storage/transform separation:** Raw data is written to `data/raw/` 
  immediately after ingestion, before any transformation. This preserves 
  source fidelity and enables re-processing without re-ingesting. The 
  curated layer in `data/curated/` is always reproducible from raw.

- **Idempotent full-refresh design:** Every pipeline run overwrites raw 
  and curated files deterministically. No state is maintained between runs. 
  This makes the pipeline safe to re-run at any time and easy to schedule 
  via cron or Airflow without risk of duplicate data.

- **Time-axis alignment (forward-fill):** The monthly RBA series is 
  forward-filled onto the daily ASX trading calendar. Each trading day 
  carries the most recently announced cash rate — reflecting what market 
  participants actually knew. No look-ahead bias is introduced.

- **Quality logging over silent drops:** All data anomalies (nulls, 
  duplicates, schema changes, API failures) are written to 
  `data/quality_log.csv` with timestamp, source, type, and disposition. 
  Records are never silently dropped.

- **RAG threshold rationale:** The volatility signal uses 15% (amber) 
  and 25% (red) annualised thresholds. The ASX 200 trades at ~14–16% 
  average annualised volatility in normal conditions; 25% has historically 
  preceded elevated post-trade operational stress events.

---

## Data Quality Issue Log

**Issue:** Yahoo Finance returns a duplicate index entry for the most 
recent trading day when the market is open mid-session (intraday snapshot 
duplicated with prior close).

**Where it appeared:** `data/raw/asx200_raw.csv`, last row.

**Fix:** Detected via `df.index.duplicated()` check in `transform.py`; 
resolved by keeping the last entry (most recent intraday value). 
Logged to `data/quality_log.csv` with disposition `DEDUPLICATED_KEEP_LAST`.

---

## Handoff Notes
*For the engineer taking this over — here's what you need to know.*

**Adding a new data source:**
1. Create a new fetch function in `pipeline/ingest.py` following the same 
   pattern as `fetch_asx200()` — it must save a raw CSV to `data/raw/` 
   and call `log_quality_event()` for any anomaly.
2. Add the corresponding load and join logic in `transform.py`.
3. Do not modify the existing ingest functions unless the source API has 
   changed — keep them stable.

**Adding a new metric:**
1. Write the plain-language definition first (add it to the Metric 
   Definitions section above).
2. Implement it in `calculate_metrics()` in `transform.py`.
3. Add a test in `tests/test_pipeline.py` covering at least the null 
   and edge-case behaviour.

**What to watch out for:**
- The RBA XLS file structure has changed twice in the past three years. 
  The schema guard in `fetch_rba_cash_rate()` will catch this and log 
  a `SCHEMA_CHANGE` event — do not ignore these.
- Yahoo Finance's free tier can return stale or incomplete data during 
  market hours. Always validate the last row's date against expected 
  trading calendar before trusting it in production.
- The `data/` directory is in `.gitignore` — never commit raw or curated 
  data files to the repo.

---

## Agent Log

**Tools used:** Claude Sonnet 4.5 (Anthropic) via API, June 2026

**Tasks delegated to the agent:**
- First draft of the `ingest.py` skeleton including argument parsing and 
  basic yfinance call
- First draft of the Plotly Dash layout structure and subplot configuration
- Boilerplate for `quality_log.py` CSV append logic

**Example of incorrect/incomplete agent output — and my correction:**

*Task:* Generate the RBA XLS ingestion logic.

*Agent output (incorrect):* The agent used `pd.read_excel(..., skiprows=4)` 
based on a generic assumption about RBA file headers, and did not include 
any schema guard or fallback logic. When run against the actual RBA F1.1 
file, the parse failed silently and returned an empty DataFrame.

*How I identified it:* I ran the generated code against the actual file 
immediately. The resulting DataFrame had 0 rows and no logged error — the 
agent had not included any empty-response check.

*What I changed:* 
  1. Changed `skiprows` to 10 after inspecting the actual XLS structure
  2. Added the `"Series ID"` column guard with positional fallback
  3. Added explicit empty-DataFrame check with a `log_quality_event()` call
  4. Added `requests.exceptions.Timeout` handling — the agent had used a 
     generic `except Exception` which would mask timeout vs HTTP errors

*Overall assessment:*
  - **Agent accelerated:** Boilerplate scaffolding (argparse setup, CSV 
    writer pattern, subplot layout) — tasks with predictable structure 
    that would have taken 20–30 minutes manually each.
  - **Human judgement was essential:** Anything touching real API 
    behaviour (RBA file format, Yahoo Finance duplicate-row behaviour, 
    timezone handling). The agent has no knowledge of the actual runtime 
    characteristics of these endpoints. Every piece of ingestion code 
    required manual testing and correction before I accepted it.
  - **Rule I applied throughout:** If the agent wrote error handling, I 
    tested the error path explicitly. Agents consistently under-specify 
    failure modes — they write happy-path code with `except Exception: pass` 
    as a safety blanket. That is not acceptable in a data pipeline that 
    feeds leadership decisions.

**Environment issue encountered:** Corporate/network SSL proxy caused 
`CERTIFICATE_VERIFY_FAILED` on RBA HTTPS requests. Fixed by fetching 
via `requests` with `verify=False` and piping HTML content to 
`pd.read_html(StringIO(...))`. `verify=False` is acceptable for 
read-only public data; in production this would be resolved by 
installing the corporate CA certificate into the trust store instead.