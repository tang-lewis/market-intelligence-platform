"""
dashboard.py — Market Intelligence Platform dashboard
Target audience: Financially literate, non-technical operations and leadership users.
Central question: "How has market activity trended over the past 90 days,
                   and is there anything we should be watching?"

RAG Signal Definition:
  GREEN:  14-day volatility < 15% annualised (normal operating conditions)
  AMBER:  14-day volatility >= 15% and < 25% (elevated — monitor closely)
  RED:    14-day volatility >= 25% annualised (high stress — flag to leadership)

  Rationale: ASX 200 historical average annualised volatility is ~14–16% in
  normal markets. The 25% threshold corresponds to conditions historically
  associated with elevated post-trade operational stress (higher settlement
  fails, margin calls, corporate action volume spikes).

Run: python dashboard/dashboard.py
Then open: http://127.0.0.1:8050
"""

import os
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import dash
from dash import dcc, html

CURATED_PATH = "data/curated/market_intelligence_curated.csv"

# RAG thresholds — see rationale in module docstring
AMBER_THRESHOLD = 0.15
RED_THRESHOLD = 0.25


def load_data() -> pd.DataFrame:
    df = pd.read_csv(CURATED_PATH, index_col="date", parse_dates=True)
    # Limit to last 90 calendar days
    cutoff = df.index.max() - pd.Timedelta(days=90)
    return df[df.index >= cutoff].copy()


def compute_rag(volatility: float) -> tuple[str, str]:
    """Returns (RAG label, hex colour) for a given volatility value."""
    if pd.isna(volatility):
        return "No Data", "#999999"
    if volatility >= RED_THRESHOLD:
        return "RED — High market stress", "#E63946"
    if volatility >= AMBER_THRESHOLD:
        return "AMBER — Elevated volatility", "#F4A261"
    return "GREEN — Normal conditions", "#2A9D8F"


def build_figure(df: pd.DataFrame) -> go.Figure:
    latest_vol = df["volatility_14d_ann"].iloc[-1]
    rag_label, rag_colour = compute_rag(latest_vol)

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.5, 0.25, 0.25],
        vertical_spacing=0.08,
        subplot_titles=[
            "ASX 200 — Index Level & 20-Day Average",
            "Market Volatility (14-Day, Annualised)",
            "RBA Cash Rate Target (%)"
        ]
    )

    # --- Row 1: ASX 200 price + rolling average ---
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Close"],
        mode="lines", name="ASX 200 Close",
        line=dict(color="#1F77B4", width=1.5),
        hovertemplate="%{x|%d %b %Y}<br>Close: %{y:,.0f}<extra></extra>"
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["rolling_avg_20d"],
        mode="lines", name="20-Day Average",
        line=dict(color="#FF7F0E", width=2, dash="dash"),
        hovertemplate="%{x|%d %b %Y}<br>20-Day Avg: %{y:,.0f}<extra></extra>"
    ), row=1, col=1)

    # --- Row 2: Volatility with RAG band shading ---
    fig.add_trace(go.Scatter(
        x=df.index, y=df["volatility_14d_ann"] * 100,
        mode="lines", name="14-Day Volatility (%)",
        line=dict(color="#9467BD", width=1.5),
        fill="tozeroy", fillcolor="rgba(148,103,189,0.15)",
        hovertemplate="%{x|%d %b %Y}<br>Volatility: %{y:.1f}%<extra></extra>"
    ), row=2, col=1)

    # Threshold lines — annotated for non-technical readers
    for threshold, label, colour in [
        (AMBER_THRESHOLD * 100, "Watch level (15%)", "#F4A261"),
        (RED_THRESHOLD * 100, "Alert level (25%)", "#E63946"),
    ]:
        fig.add_hline(
            y=threshold, row=2, col=1,
            line=dict(color=colour, dash="dot", width=1.2),
            annotation_text=label,
            annotation_position="right"
        )

    # --- Row 3: RBA Cash Rate ---
    fig.add_trace(go.Scatter(
        x=df.index, y=df["cash_rate_target"],
        mode="lines+markers", name="RBA Cash Rate (%)",
        line=dict(color="#2CA02C", width=2),
        marker=dict(size=5),
        hovertemplate="%{x|%d %b %Y}<br>Cash Rate: %{y:.2f}%<extra></extra>"
    ), row=3, col=1)

    # Divergence annotation — highlight if last rate move was a cut vs rising volatility
    last_rate_change = df["rba_mom_change_bps"].dropna()
    if not last_rate_change.empty:
        last_change_date = last_rate_change.index[-1]
        last_change_val = last_rate_change.iloc[-1]
        if abs(last_change_val) >= 25:  # only annotate material moves (>=25bps)
            direction = "Cut" if last_change_val < 0 else "Hike"
            fig.add_annotation(
                x=last_change_date,
                y=df.loc[last_change_date, "cash_rate_target"],
                text=f"RBA {direction} {abs(last_change_val):.0f}bps",
                showarrow=True, arrowhead=2, row=3, col=1,
                font=dict(size=10)
            )

    fig.update_layout(
        title=dict(
            text=(
                f"Market Intelligence — Last 90 Days  |  "
                f"<span style='color:{rag_colour}'>● {rag_label}</span>"
            ),
            font=dict(size=16)
        ),
        height=750,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        plot_bgcolor="#FAFAFA",
        paper_bgcolor="#FFFFFF",
        margin=dict(l=60, r=80, t=80, b=40)
    )

    fig.update_yaxes(title_text="Index Level", row=1, col=1, tickformat=",")
    fig.update_yaxes(title_text="Volatility (%)", row=2, col=1, tickformat=".1f")
    fig.update_yaxes(title_text="Rate (%)", row=3, col=1, tickformat=".2f")
    fig.update_xaxes(title_text="Date", row=3, col=1)

    return fig


def create_app() -> dash.Dash:
    df = load_data()
    fig = build_figure(df)

    latest_vol = df["volatility_14d_ann"].iloc[-1]
    rag_label, rag_colour = compute_rag(latest_vol)
    latest_date = df.index.max().strftime("%d %b %Y")
    latest_close = df["Close"].iloc[-1]
    latest_rate = df["cash_rate_target"].iloc[-1]

    app = dash.Dash(__name__, title="Market Intelligence Platform")
    app.layout = html.Div([
        html.Div([
            html.H1("Market Intelligence Platform", style={"margin": "0", "fontSize": "22px"}),
            html.P(
                f"Data as at {latest_date}  |  ASX 200: {latest_close:,.0f}  "
                f"|  RBA Cash Rate: {latest_rate:.2f}%",
                style={"color": "#555", "margin": "4px 0 0 0", "fontSize": "13px"}
            ),
        ], style={"padding": "20px 30px 10px", "borderBottom": "1px solid #eee"}),

        html.Div([
            html.Div([
                html.Div("Market Status", style={"fontSize": "11px", "color": "#888", "marginBottom": "4px"}),
                html.Div(rag_label, style={
                    "fontSize": "14px", "fontWeight": "bold",
                    "color": rag_colour, "padding": "6px 12px",
                    "border": f"1px solid {rag_colour}",
                    "borderRadius": "4px", "display": "inline-block"
                })
            ], style={"padding": "10px 30px"})
        ]),

        dcc.Graph(figure=fig, style={"padding": "0 20px"}, config={"displayModeBar": False}),

        html.Div([
            html.P(
                "Signal definition: GREEN < 15% annualised volatility (normal) | "
                "AMBER 15–25% (elevated) | RED > 25% (high stress). "
                "Volatility based on 14-day rolling standard deviation of daily log returns, annualised.",
                style={"fontSize": "11px", "color": "#aaa", "padding": "0 30px 20px"}
            )
        ])
    ], style={"fontFamily": "Arial, sans-serif", "backgroundColor": "#fff"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=False, port=8050)