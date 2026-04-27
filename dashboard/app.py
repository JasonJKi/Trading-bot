"""Streamlit dashboard. Pure reader — never moves money."""
from __future__ import annotations

import hmac
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from src.config import get_settings
from src.core import metrics
from src.core.store import EquitySnapshot, Signal, Trade, init_db, session_scope


st.set_page_config(page_title="Trading Bot Dashboard", layout="wide")


def _password_gate() -> bool:
    """Block the rest of the app until the user enters the right password.

    The password is read from the DASHBOARD_PASSWORD env var (set as a Fly secret).
    If the env var is empty / unset, auth is disabled — useful for local dev only.
    """
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected:
        st.warning(
            "DASHBOARD_PASSWORD is not set — dashboard is open to anyone with the URL. "
            "Set the secret on Fly to enable auth.",
            icon="⚠️",
        )
        return True

    if st.session_state.get("auth_ok"):
        return True

    st.title("Trading Bot Dashboard")
    pw = st.text_input("Password", type="password")
    if pw and hmac.compare_digest(pw, expected):
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pw:
        st.error("Incorrect password.")
    return False


@st.cache_data(ttl=30)
def _load_equity() -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                EquitySnapshot.ts,
                EquitySnapshot.strategy_id,
                EquitySnapshot.cash,
                EquitySnapshot.position_value,
                EquitySnapshot.total_equity,
            )
        ).all()
    return pd.DataFrame(rows, columns=["ts", "strategy_id", "cash", "position_value", "total_equity"])


@st.cache_data(ttl=30)
def _load_trades() -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                Trade.ts,
                Trade.strategy_id,
                Trade.symbol,
                Trade.side,
                Trade.qty,
                Trade.price,
                Trade.notional,
                Trade.order_id,
            )
        ).all()
    return pd.DataFrame(
        rows,
        columns=["ts", "strategy_id", "symbol", "side", "qty", "price", "notional", "order_id"],
    )


@st.cache_data(ttl=30)
def _load_signals() -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                Signal.ts,
                Signal.strategy_id,
                Signal.symbol,
                Signal.direction,
                Signal.strength,
            )
        ).all()
    return pd.DataFrame(rows, columns=["ts", "strategy_id", "symbol", "direction", "strength"])


def _trade_pnls(trades: pd.DataFrame) -> pd.Series:
    """Naive realized PnL per round trip (buy -> sell at avg price). Approximate."""
    if trades.empty:
        return pd.Series(dtype=float)
    pnls = []
    for (_, _), grp in trades.groupby(["strategy_id", "symbol"]):
        grp = grp.sort_values("ts")
        position = 0.0
        cost = 0.0
        for _, row in grp.iterrows():
            if row["side"] == "buy":
                cost += row["qty"] * row["price"]
                position += row["qty"]
            else:
                if position > 0:
                    avg = cost / position
                    pnls.append(row["qty"] * (row["price"] - avg))
                    position -= row["qty"]
                    cost -= row["qty"] * avg
    return pd.Series(pnls)


def _equity_series(eq_df: pd.DataFrame, strategy_id: str) -> pd.Series:
    sub = eq_df[eq_df["strategy_id"] == strategy_id].sort_values("ts")
    if sub.empty:
        return pd.Series(dtype=float)
    return pd.Series(sub["total_equity"].values, index=pd.to_datetime(sub["ts"]))


def _render_metrics_card(report: metrics.PerfReport) -> None:
    cols = st.columns(4)
    cols[0].metric("Total return", f"{report.total_return * 100:.2f}%")
    cols[1].metric("CAGR", f"{report.cagr * 100:.2f}%")
    cols[2].metric("Sharpe", f"{report.sharpe:.2f}")
    cols[3].metric("Sortino", f"{report.sortino:.2f}")
    cols = st.columns(4)
    cols[0].metric("Max DD", f"{report.max_drawdown * 100:.2f}%")
    cols[1].metric("Win rate", f"{report.win_rate * 100:.1f}%")
    cols[2].metric("Avg win", f"${report.avg_win:.2f}")
    cols[3].metric("Expectancy", f"${report.expectancy:.2f}")


def _render_bot_tab(strategy_id: str, eq_df: pd.DataFrame, trades_df: pd.DataFrame) -> None:
    eq = _equity_series(eq_df, strategy_id)
    bot_trades = trades_df[trades_df["strategy_id"] == strategy_id]
    pnls = _trade_pnls(bot_trades)
    report = metrics.report(eq, pnls)

    _render_metrics_card(report)

    if eq.empty:
        st.info("No equity snapshots yet. Run the orchestrator at least once.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, mode="lines", name="Equity"))
        fig.update_layout(title="Equity curve", height=350, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

        dd = metrics.drawdown_curve(eq)
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, mode="lines", fill="tozeroy"))
        fig_dd.update_layout(
            title="Drawdown (%)", height=250, margin=dict(l=10, r=10, t=40, b=10)
        )
        st.plotly_chart(fig_dd, use_container_width=True)

    st.subheader("Trades")
    if bot_trades.empty:
        st.write("None.")
    else:
        st.dataframe(
            bot_trades.sort_values("ts", ascending=False).head(200), use_container_width=True
        )


def _render_leaderboard(eq_df: pd.DataFrame, trades_df: pd.DataFrame) -> None:
    if eq_df.empty:
        st.info("No data yet.")
        return
    rows = []
    eq_by_strategy: dict[str, pd.Series] = {}
    for sid in eq_df["strategy_id"].unique():
        eq = _equity_series(eq_df, sid)
        eq_by_strategy[sid] = eq
        pnls = _trade_pnls(trades_df[trades_df["strategy_id"] == sid])
        r = metrics.report(eq, pnls)
        rows.append(
            {
                "strategy": sid,
                "total_return_%": r.total_return * 100,
                "sharpe": r.sharpe,
                "sortino": r.sortino,
                "max_dd_%": r.max_drawdown * 100,
                "win_rate_%": r.win_rate * 100,
                "expectancy_$": r.expectancy,
            }
        )
    df = pd.DataFrame(rows).set_index("strategy")
    st.subheader("Leaderboard")
    st.dataframe(df.style.format(precision=2), use_container_width=True)

    st.subheader("Strategy correlation")
    corr = metrics.correlation_matrix(eq_by_strategy)
    if not corr.empty:
        fig = px.imshow(corr, text_auto=".2f", aspect="auto", color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
        fig.update_layout(height=400, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    if not _password_gate():
        st.stop()
    settings = get_settings()
    st.title("Trading Bot Dashboard")
    mode = "PAPER" if settings.alpaca_paper else "LIVE"
    color = "green" if settings.alpaca_paper else "red"
    st.markdown(
        f"Mode: <span style='color:{color};font-weight:600'>{mode}</span>", unsafe_allow_html=True
    )

    if st.button("Refresh"):
        _load_equity.clear()
        _load_trades.clear()
        _load_signals.clear()

    eq_df = _load_equity()
    trades_df = _load_trades()
    strategies = sorted(eq_df["strategy_id"].unique()) if not eq_df.empty else []

    tab_names = ["Leaderboard"] + strategies
    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_leaderboard(eq_df, trades_df)

    for sid, tab in zip(strategies, tabs[1:]):
        with tab:
            st.header(sid)
            _render_bot_tab(sid, eq_df, trades_df)


if __name__ == "__main__":
    main()
