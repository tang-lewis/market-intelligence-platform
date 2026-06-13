"""
transform.py — Curation and metric calculation for Market Intelligence Platform.

Metric definitions (plain language — defined before this code was written):
  1. 20-Day Rolling Average Price: 20-trading-day rolling mean of ASX 200 Close.
  2. 14-Day Annualised Volatility: Annualised std dev of daily log returns over 14 days.
  3. RBA MoM Rate Change (bps): Month-on-month change in RBA Cash Rate Target, in basis points.

Time-axis alignment strategy:
  - ASX 200 is daily (trading days only, Sydney/AEST implied by Yahoo Finance).
  - RBA Cash Rate is monthly (effective date, AEST).
  - Both are stored as tz-naive dates after ingestion.
  - Join method: forward-fill the monthly RBA series onto the daily ASX calendar.
    This means each trading day carries the most recently announced cash rate —
    which correctly reflects what market participants knew at that point in time.
    No look-ahead bias is introduced.

Data quality issue encountered:
  Yahoo Finance occasionally returns a duplicate index entry for the last trading day
  when the market is mid-session. Fix: deduplicate on index, keeping the last entry
  (most recent intraday snapshot). Logged to quality_log.csv.
"""

import logging
import os

import numpy as np
import pandas as pd

from pipeline.quality_log import log_quality_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = "data/raw"
CURATED_DIR = "data/curated"


def load_asx200() -> pd.DataFrame:
    path = os.path.join(RAW_DIR, "asx200_raw.csv")
    df = pd.read_csv(path, index_col="date", parse_dates=True)
    df.index = pd.to_datetime(df.index)

    # Deduplication — Yahoo Finance known issue: last trading day may duplicate mid-session
    dupes = df.index.duplicated().sum()
    if dupes > 0:
        log_quality_event(
            source="yahoo_finance",
            event_type="DUPLICATE_INDEX",
            detail=f"{dupes} duplicate date entries found; keeping last (most recent intraday)",
            disposition="DEDUPLICATED_KEEP_LAST"
        )
        logger.warning("Deduplicating %d ASX 200 rows", dupes)
        df = df[~df.index.duplicated(keep="last")]

    return df.sort_index()


def load_rba() -> pd.DataFrame:
    path = os.path.join(RAW_DIR, "rba_cash_rate_raw.csv")
    df = pd.read_csv(path, index_col="date", parse_dates=True)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def calculate_metrics(asx: pd.DataFrame, rba: pd.DataFrame) -> pd.DataFrame:
    """
    Join ASX 200 and RBA data, then calculate all three metrics.
    Returns a single curated DataFrame on a daily trading-day index.
    """

    # --- Metric 1: 20-Day Rolling Average Price ---
    asx["rolling_avg_20d"] = asx["Close"].rolling(window=20, min_periods=10).mean()

    # --- Metric 2: 14-Day Annualised Volatility ---
    log_returns = np.log(asx["Close"] / asx["Close"].shift(1))
    asx["volatility_14d_ann"] = log_returns.rolling(window=14, min_periods=7).std() * np.sqrt(252)

    # --- Time-axis join: forward-fill monthly RBA onto daily ASX calendar ---
    # Reindex RBA to daily frequency, then forward-fill to carry rate forward
    # until next announcement. This is the standard approach for macro series joins
    # and avoids look-ahead bias.
    rba_daily = rba[["cash_rate_target"]].reindex(asx.index, method="ffill")

    # --- Metric 3: Month-on-Month RBA Rate Change (bps) ---
    # Resample to get end-of-month rate, then compute month-on-month delta
    rba_monthly = rba["cash_rate_target"].resample("ME").last()
    rba_mom_bps = (rba_monthly.diff() * 100).rename("rba_mom_change_bps")

    # Map MoM change back to daily (same forward-fill logic)
    rba_mom_daily = rba_mom_bps.reindex(asx.index, method="ffill")

    # Combine all into curated frame
    curated = asx[["Open", "High", "Low", "Close", "Volume",
                   "rolling_avg_20d", "volatility_14d_ann"]].copy()
    curated["cash_rate_target"] = rba_daily["cash_rate_target"]
    curated["rba_mom_change_bps"] = rba_mom_daily

    logger.info("Metrics calculated. Curated dataset: %d rows x %d cols", *curated.shape)
    return curated


def save_curated(df: pd.DataFrame) -> None:
    os.makedirs(CURATED_DIR, exist_ok=True)
    path = os.path.join(CURATED_DIR, "market_intelligence_curated.csv")
    df.to_csv(path)
    logger.info("Curated data saved: %s", path)


def main():
    asx = load_asx200()
    rba = load_rba()
    curated = calculate_metrics(asx, rba)
    save_curated(curated)


if __name__ == "__main__":
    main()