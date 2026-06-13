"""
ingest.py — Data ingestion for Market Intelligence Platform
Pulls ASX 200 from Yahoo Finance and RBA Cash Rate from rba.gov.au.
Saves raw data to data/raw/ without any transformation.
Re-run at any time; output is idempotent (full refresh, configurable lookback).

Usage:
    python pipeline/ingest.py --days 90
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

from pipeline.quality_log import log_quality_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

RAW_DIR = "data/raw"
ASX_TICKER = "^AXJO"

# RBA Table F1.1 — Interbank Overnight Cash Rate (monthly series)
# URL is stable; column name confirmed against RBA website as of June 2026
RBA_URL = (
    "https://www.rba.gov.au/statistics/tables/xls-hist/f1-1hist.xls"
)
RBA_SHEET = "Data"
RBA_RATE_COL = "Cash Rate Target"


def fetch_asx200(days: int) -> pd.DataFrame:
    """
    Fetch ASX 200 daily OHLCV from Yahoo Finance.
    Returns a DataFrame with a UTC-normalised DatetimeIndex.
    Raises on empty response — do not silently return an empty frame.
    """
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days + 10)  # buffer for weekends/holidays

    logger.info("Fetching ASX 200 from Yahoo Finance (%s to %s)...", start.date(), end.date())

    try:
        df = yf.download(
            ASX_TICKER,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True
        )
    except Exception as e:
        # Network or API failure — log and raise; do not swallow
        log_quality_event(
            source="yahoo_finance",
            event_type="API_ERROR",
            detail=str(e),
            disposition="PIPELINE_HALTED"
        )
        logger.error("Yahoo Finance API call failed: %s", e)
        raise

    if df.empty:
        # Empty response is a data quality event, not just an edge case
        log_quality_event(
            source="yahoo_finance",
            event_type="EMPTY_RESPONSE",
            detail=f"No data returned for ticker {ASX_TICKER}",
            disposition="PIPELINE_HALTED"
        )
        raise ValueError(f"Empty response from Yahoo Finance for {ASX_TICKER}")

    # Normalise index to UTC date (Yahoo returns tz-aware for ASX)
    df.index = pd.to_datetime(df.index).tz_localize(None)  # strip tz — store as date-only
    df.index.name = "date"

    # Check for required columns — schema guard
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    missing = required_cols - set(df.columns)
    if missing:
        log_quality_event(
            source="yahoo_finance",
            event_type="SCHEMA_CHANGE",
            detail=f"Missing expected columns: {missing}",
            disposition="PIPELINE_HALTED"
        )
        raise ValueError(f"Yahoo Finance response missing columns: {missing}")

    # Log any null closing prices — do not drop silently
    null_closes = df["Close"].isnull().sum()
    if null_closes > 0:
        log_quality_event(
            source="yahoo_finance",
            event_type="NULL_VALUES",
            detail=f"{null_closes} null Close values detected",
            disposition="RETAINED_FOR_REVIEW"
        )
        logger.warning("%d null Close values in ASX 200 data — retained in raw, flagged in quality log", null_closes)

    logger.info("ASX 200 fetch complete: %d rows", len(df))
    return df


def fetch_rba_cash_rate() -> pd.DataFrame:
    """
    Fetch RBA Interbank Overnight Cash Rate from rba.gov.au (XLS).
    Returns a monthly DataFrame with columns: [date, cash_rate_target].
    The RBA file uses Australian Eastern Time implicitly — no tz conversion needed
    for monthly series; we store as date-only.
    """
    logger.info("Fetching RBA Cash Rate from rba.gov.au...")

    try:
        response = requests.get(RBA_URL, timeout=30)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        log_quality_event(
            source="rba",
            event_type="API_TIMEOUT",
            detail=f"Request to {RBA_URL} timed out after 30s",
            disposition="PIPELINE_HALTED"
        )
        logger.error("RBA data fetch timed out")
        raise
    except requests.exceptions.HTTPError as e:
        log_quality_event(
            source="rba",
            event_type="HTTP_ERROR",
            detail=str(e),
            disposition="PIPELINE_HALTED"
        )
        logger.error("RBA HTTP error: %s", e)
        raise

    # Write raw XLS to disk before parsing — preserve source fidelity
    raw_path = os.path.join(RAW_DIR, "rba_cash_rate_raw.xls")
    with open(raw_path, "wb") as f:
        f.write(response.content)
    logger.info("RBA raw file saved to %s", raw_path)

    try:
        # RBA XLS: skip header rows; the rate series starts after metadata rows
        xls = pd.read_excel(raw_path, sheet_name=RBA_SHEET, skiprows=10, engine="xlrd")
    except Exception as e:
        log_quality_event(
            source="rba",
            event_type="PARSE_ERROR",
            detail=f"XLS parse failed: {e}",
            disposition="PIPELINE_HALTED"
        )
        raise

    # Locate date and rate columns — guard against schema changes
    if "Series ID" not in xls.columns:
        # Fallback: assume first two columns are date and rate
        log_quality_event(
            source="rba",
            event_type="SCHEMA_CHANGE",
            detail="Expected 'Series ID' column not found; using positional fallback",
            disposition="SCHEMA_FALLBACK_APPLIED"
        )
        xls.columns = ["date", "cash_rate_target"] + list(xls.columns[2:])
    else:
        xls = xls.rename(columns={xls.columns[0]: "date", RBA_RATE_COL: "cash_rate_target"})

    df = xls[["date", "cash_rate_target"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["cash_rate_target"] = pd.to_numeric(df["cash_rate_target"], errors="coerce")

    null_rates = df["cash_rate_target"].isnull().sum()
    if null_rates > 0:
        log_quality_event(
            source="rba",
            event_type="NULL_VALUES",
            detail=f"{null_rates} null cash_rate_target values after parse",
            disposition="RETAINED_FOR_REVIEW"
        )

    df = df.set_index("date").sort_index()
    logger.info("RBA Cash Rate fetch complete: %d monthly observations", len(df))
    return df


def save_raw(df: pd.DataFrame, filename: str) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, filename)
    df.to_csv(path)
    logger.info("Raw data saved: %s (%d rows)", path, len(df))


def main():
    parser = argparse.ArgumentParser(description="Ingest ASX 200 and RBA Cash Rate data")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in calendar days")
    args = parser.parse_args()

    asx = fetch_asx200(days=args.days)
    save_raw(asx, "asx200_raw.csv")

    rba = fetch_rba_cash_rate()
    save_raw(rba, "rba_cash_rate_raw.csv")

    logger.info("Ingestion complete.")


if __name__ == "__main__":
    main()