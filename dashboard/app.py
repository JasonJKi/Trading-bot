"""Streamlit dashboard. Pure reader — never moves money."""
from __future__ import annotations

import hmac
import os
import platform
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from src.config import get_settings
from src.core import metrics
from src.core.store import (
    AuditEvent,
    BotPosition,
    BotStatus,
    EquitySnapshot,
    Order,
    Signal,
    Trade,
    init_db,
    session_scope,
)


st.set_page_config(
    page_title="Trading Bot",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={"About": "Multi-strategy paper-first trading bot."},
)


# --- styling --------------------------------------------------------------
_CSS = """
<style>
  /* Tighten Streamlit's default padding so the dashboard feels denser. */
  section.main > div.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }

  /* Card primitive used throughout. */
  .card {
    background: #161B22;
    border: 1px solid #21262D;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
  }
  .card h3 { margin-top: 0; margin-bottom: 8px; }
  .card .muted { color: #8B949E; font-size: 0.85em; }
  .card .accent-momentum { border-left: 4px solid #2F81F7; }
  .card .accent-mean_reversion { border-left: 4px solid #DB6D28; }
  .card .accent-congress { border-left: 4px solid #3FB950; }
  .card .accent-sentiment { border-left: 4px solid #BC8CFF; }

  /* Status pills. */
  .pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.75em;
    font-weight: 600;
    letter-spacing: 0.04em;
    vertical-align: middle;
  }
  .pill-paper { background: #1F6FEB22; color: #58A6FF; border: 1px solid #1F6FEB55; }
  .pill-live  { background: #DA363322; color: #F85149; border: 1px solid #DA363355; }
  .pill-ok    { background: #23863622; color: #3FB950; border: 1px solid #23863655; }
  .pill-warn  { background: #D2992422; color: #D29922; border: 1px solid #D2992455; }
  .pill-bad   { background: #DA363322; color: #F85149; border: 1px solid #DA363355; }
  .pill-neutral { background: #30363D; color: #C9D1D9; }

  /* Big metric tiles. */
  .tile {
    background: linear-gradient(180deg, #161B22 0%, #0D1117 100%);
    border: 1px solid #21262D;
    border-radius: 10px;
    padding: 14px 18px;
    height: 100%;
  }
  .tile .label { color: #8B949E; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.06em; }
  .tile .value { font-size: 1.6em; font-weight: 600; margin-top: 4px; color: #F0F6FC; }
  .tile .delta-pos { color: #3FB950; font-size: 0.85em; }
  .tile .delta-neg { color: #F85149; font-size: 0.85em; }

  /* Section headers. */
  .section-h {
    color: #8B949E;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.78em;
    font-weight: 600;
    margin: 18px 0 8px 0;
  }
</style>
"""

PALETTE = {
    "momentum": "#2F81F7",
    "mean_reversion": "#DB6D28",
    "congress": "#3FB950",
    "sentiment": "#BC8CFF",
}


def _tile(label: str, value: str, delta: str | None = None, delta_kind: str = "neutral") -> str:
    delta_html = ""
    if delta:
        cls = "delta-pos" if delta_kind == "pos" else "delta-neg" if delta_kind == "neg" else "muted"
        delta_html = f'<div class="{cls}">{delta}</div>'
    return f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div>{delta_html}</div>'


# --- auth -----------------------------------------------------------------
def _password_gate() -> bool:
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected:
        st.warning(
            "DASHBOARD_PASSWORD is not set — dashboard is open to anyone with the URL.",
            icon="⚠️",
        )
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.title("Trading Bot")
    pw = st.text_input("Password", type="password")
    if pw and hmac.compare_digest(pw, expected):
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pw:
        st.error("Incorrect password.")
    return False


# --- DB loaders -----------------------------------------------------------
@st.cache_data(ttl=15)
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
    return pd.DataFrame(
        rows, columns=["ts", "strategy_id", "cash", "position_value", "total_equity"]
    )


@st.cache_data(ttl=15)
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


@st.cache_data(ttl=15)
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


@st.cache_data(ttl=15)
def _load_orders() -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                Order.ts,
                Order.strategy_id,
                Order.symbol,
                Order.side,
                Order.qty,
                Order.status,
                Order.filled_qty,
                Order.filled_avg_price,
                Order.client_order_id,
                Order.broker_order_id,
                Order.error,
            )
        ).all()
    return pd.DataFrame(
        rows,
        columns=[
            "ts", "strategy_id", "symbol", "side", "qty", "status",
            "filled_qty", "filled_avg_price", "client_order_id",
            "broker_order_id", "error",
        ],
    )


@st.cache_data(ttl=15)
def _load_bot_positions() -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                BotPosition.strategy_id,
                BotPosition.symbol,
                BotPosition.qty,
                BotPosition.avg_price,
                BotPosition.cost_basis,
                BotPosition.opened_at,
                BotPosition.updated_at,
            )
        ).all()
    return pd.DataFrame(
        rows,
        columns=["strategy_id", "symbol", "qty", "avg_price", "cost_basis", "opened_at", "updated_at"],
    )


@st.cache_data(ttl=15)
def _load_audit(limit: int = 200) -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                AuditEvent.ts,
                AuditEvent.kind,
                AuditEvent.severity,
                AuditEvent.strategy_id,
                AuditEvent.message,
            ).order_by(AuditEvent.ts.desc()).limit(limit)
        ).all()
    return pd.DataFrame(rows, columns=["ts", "kind", "severity", "strategy_id", "message"])


@st.cache_data(ttl=10)
def _load_bot_status() -> pd.DataFrame:
    init_db()
    with session_scope() as sess:
        rows = sess.execute(
            select(
                BotStatus.strategy_id,
                BotStatus.state,
                BotStatus.reason,
                BotStatus.paper_validated_at,
                BotStatus.updated_at,
            )
        ).all()
    return pd.DataFrame(
        rows,
        columns=["strategy_id", "state", "reason", "paper_validated_at", "updated_at"],
    )


@st.cache_data(ttl=20)
def _load_account() -> dict | None:
    settings = get_settings()
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        return None
    try:
        from alpaca.trading.client import TradingClient

        c = TradingClient(
            settings.alpaca_api_key, settings.alpaca_api_secret, paper=settings.alpaca_paper
        )
        a = c.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
            "last_equity": float(a.last_equity),
            "status": str(a.status),
        }
    except Exception as exc:
        return {"error": str(exc)}


@st.cache_data(ttl=20)
def _load_positions() -> pd.DataFrame:
    settings = get_settings()
    if not (settings.alpaca_api_key and settings.alpaca_api_secret):
        return pd.DataFrame()
    try:
        from alpaca.trading.client import TradingClient

        c = TradingClient(
            settings.alpaca_api_key, settings.alpaca_api_secret, paper=settings.alpaca_paper
        )
        positions = c.get_all_positions()
        if not positions:
            return pd.DataFrame()
        rows = [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc_%": float(p.unrealized_plpc) * 100,
                "side": str(p.side),
            }
            for p in positions
        ]
        return pd.DataFrame(rows)
    except Exception as exc:
        return pd.DataFrame([{"error": str(exc)}])


# --- helpers --------------------------------------------------------------
def _trade_pnls(trades: pd.DataFrame) -> pd.Series:
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


def _next_run(schedule: dict) -> datetime | None:
    try:
        from apscheduler.triggers.cron import CronTrigger

        return CronTrigger(**schedule, timezone="UTC").get_next_fire_time(
            None, datetime.now(timezone.utc)
        )
    except Exception:
        return None


def _enabled_bots() -> list:
    from src.core.orchestrator import load_enabled_bots

    return load_enabled_bots(get_settings())


def _format_delta(now: datetime, future: datetime | None) -> str:
    if future is None:
        return "—"
    delta = future - now
    secs = int(delta.total_seconds())
    if secs < 0:
        return "now"
    if secs < 3600:
        return f"in {secs // 60}m"
    if secs < 86400:
        return f"in {secs // 3600}h {(secs % 3600) // 60}m"
    return f"in {secs // 86400}d {(secs % 86400) // 3600}h"


# --- header / status row --------------------------------------------------
def _render_header(settings) -> None:
    mode = "PAPER" if settings.alpaca_paper else "LIVE"
    pill_class = "pill-paper" if settings.alpaca_paper else "pill-live"
    region = os.environ.get("FLY_REGION", "local")
    machine = os.environ.get("FLY_MACHINE_ID", platform.node())[:14]

    cols = st.columns([6, 1])
    with cols[0]:
        st.markdown(
            f"## Trading Bot "
            f"<span class='pill {pill_class}'>{mode}</span> "
            f"<span class='pill pill-neutral'>{region}</span> "
            f"<span class='pill pill-neutral'>{machine}</span>",
            unsafe_allow_html=True,
        )
    with cols[1]:
        if st.button("↻ Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


def _render_status_tiles(account: dict | None) -> None:
    cols = st.columns(4)
    if account is None or "error" in (account or {}):
        msg = "no creds" if account is None else f"error: {account['error'][:30]}"
        cols[0].markdown(_tile("Account equity", "—", msg, "neg"), unsafe_allow_html=True)
        cols[1].markdown(_tile("Cash", "—"), unsafe_allow_html=True)
        cols[2].markdown(_tile("Buying power", "—"), unsafe_allow_html=True)
        cols[3].markdown(_tile("Status", "offline"), unsafe_allow_html=True)
        return
    delta = account["equity"] - account["last_equity"]
    delta_pct = delta / account["last_equity"] * 100 if account["last_equity"] else 0
    delta_kind = "pos" if delta >= 0 else "neg"
    sign = "+" if delta >= 0 else ""
    cols[0].markdown(
        _tile(
            "Account equity",
            f"${account['equity']:,.2f}",
            f"{sign}${delta:,.2f} ({sign}{delta_pct:.2f}%)",
            delta_kind,
        ),
        unsafe_allow_html=True,
    )
    cols[1].markdown(
        _tile("Cash", f"${account['cash']:,.2f}"), unsafe_allow_html=True
    )
    cols[2].markdown(
        _tile("Buying power", f"${account['buying_power']:,.2f}"), unsafe_allow_html=True
    )
    cols[3].markdown(
        _tile("Account status", account["status"]), unsafe_allow_html=True
    )


# --- overview tab ---------------------------------------------------------
def _render_risk_caps(settings) -> None:
    st.markdown("<div class='section-h'>Risk caps</div>", unsafe_allow_html=True)
    cols = st.columns(4)
    cols[0].markdown(_tile("Per-bot cap", f"${settings.per_bot_cap:,.0f}"), unsafe_allow_html=True)
    cols[1].markdown(
        _tile("Per-position", f"{settings.per_position_pct * 100:.1f}%"), unsafe_allow_html=True
    )
    cols[2].markdown(
        _tile("Global DD halt", f"{settings.global_max_drawdown * 100:.0f}%"), unsafe_allow_html=True
    )
    cols[3].markdown(
        _tile("Starting equity", f"${settings.account_starting_equity:,.0f}"),
        unsafe_allow_html=True,
    )


def _render_bot_cards(trades_df: pd.DataFrame, signals_df: pd.DataFrame) -> None:
    st.markdown("<div class='section-h'>Bots running</div>", unsafe_allow_html=True)
    bots = _enabled_bots()
    if not bots:
        st.warning("No bots enabled. Set ENABLED_BOTS in your environment.")
        return
    now = datetime.now(timezone.utc)
    cols = st.columns(min(len(bots), 3))
    for i, bot in enumerate(bots):
        nxt = _next_run(bot.schedule)
        countdown = _format_delta(now, nxt)
        nxt_str = nxt.strftime("%a %H:%M UTC") if nxt else "—"
        try:
            universe = bot.universe()
        except Exception:
            universe = []
        n_trades = (
            int((trades_df["strategy_id"] == bot.id).sum()) if not trades_df.empty else 0
        )
        n_signals = (
            int((signals_df["strategy_id"] == bot.id).sum()) if not signals_df.empty else 0
        )
        accent_class = f"accent-{bot.id}"
        universe_str = ", ".join(universe[:8]) + ("…" if len(universe) > 8 else "")
        with cols[i % len(cols)]:
            st.markdown(
                f"""
                <div class="card {accent_class}">
                  <h3>{bot.name}</h3>
                  <div class="muted"><code>{bot.id}</code></div>
                  <hr style="border-color:#21262D;margin:10px 0">
                  <div style="display:flex;justify-content:space-between">
                    <div>
                      <div class="muted">Next run</div>
                      <div style="font-size:1.1em;font-weight:600">{countdown}</div>
                      <div class="muted" style="font-size:0.8em">{nxt_str}</div>
                    </div>
                    <div style="text-align:right">
                      <div class="muted">Activity</div>
                      <div>{n_signals} signals</div>
                      <div>{n_trades} trades</div>
                    </div>
                  </div>
                  <hr style="border-color:#21262D;margin:10px 0">
                  <div class="muted">Universe ({len(universe)})</div>
                  <div style="font-size:0.85em">{universe_str or '—'}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_account_chart(account: dict | None) -> None:
    if account is None or "error" in (account or {}):
        return
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number+delta",
            value=account["equity"],
            number={"prefix": "$", "valueformat": ",.0f"},
            delta={
                "reference": account["last_equity"],
                "valueformat": ",.0f",
                "increasing": {"color": "#3FB950"},
                "decreasing": {"color": "#F85149"},
            },
            gauge={
                "axis": {
                    "range": [0, max(account["equity"] * 1.4, account["last_equity"] * 1.2)],
                    "tickcolor": "#8B949E",
                },
                "bar": {"color": "#2F81F7"},
                "bgcolor": "#0D1117",
                "borderwidth": 0,
            },
            title={"text": "Account equity vs prior close"},
        )
    )
    fig.update_layout(
        height=260,
        margin=dict(l=20, r=20, t=40, b=10),
        paper_bgcolor="#161B22",
        font={"color": "#F0F6FC"},
    )
    st.plotly_chart(fig, use_container_width=True)


# --- bot deep-dive --------------------------------------------------------
def _render_metrics_card(report: metrics.PerfReport) -> None:
    cols = st.columns(4)
    cols[0].markdown(
        _tile("Total return", f"{report.total_return * 100:.2f}%"), unsafe_allow_html=True
    )
    cols[1].markdown(_tile("CAGR", f"{report.cagr * 100:.2f}%"), unsafe_allow_html=True)
    cols[2].markdown(_tile("Sharpe", f"{report.sharpe:.2f}"), unsafe_allow_html=True)
    cols[3].markdown(_tile("Sortino", f"{report.sortino:.2f}"), unsafe_allow_html=True)
    cols = st.columns(4)
    cols[0].markdown(
        _tile("Max DD", f"{report.max_drawdown * 100:.2f}%"), unsafe_allow_html=True
    )
    cols[1].markdown(_tile("Win rate", f"{report.win_rate * 100:.1f}%"), unsafe_allow_html=True)
    cols[2].markdown(_tile("Avg win", f"${report.avg_win:.2f}"), unsafe_allow_html=True)
    cols[3].markdown(_tile("Expectancy", f"${report.expectancy:.2f}"), unsafe_allow_html=True)


def _render_bot_tab(strategy_id: str, eq_df: pd.DataFrame, trades_df: pd.DataFrame) -> None:
    eq = _equity_series(eq_df, strategy_id)
    bot_trades = trades_df[trades_df["strategy_id"] == strategy_id]
    pnls = _trade_pnls(bot_trades)
    report = metrics.report(eq, pnls)

    _render_metrics_card(report)

    if not eq.empty:
        color = PALETTE.get(strategy_id, "#2F81F7")
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=eq.index,
                y=eq.values,
                mode="lines",
                line=dict(color=color, width=2),
                fill="tozeroy",
                fillcolor=color + "22",
                name="Equity",
            )
        )
        fig.update_layout(
            title="Equity curve",
            height=320,
            margin=dict(l=10, r=10, t=40, b=10),
            paper_bgcolor="#0D1117",
            plot_bgcolor="#0D1117",
            font=dict(color="#F0F6FC"),
            xaxis=dict(gridcolor="#21262D"),
            yaxis=dict(gridcolor="#21262D"),
        )
        st.plotly_chart(fig, use_container_width=True)

        dd = metrics.drawdown_curve(eq)
        fig_dd = go.Figure()
        fig_dd.add_trace(
            go.Scatter(
                x=dd.index,
                y=dd.values * 100,
                mode="lines",
                fill="tozeroy",
                line=dict(color="#F85149", width=1.5),
                fillcolor="#F8514922",
            )
        )
        fig_dd.update_layout(
            title="Drawdown (%)",
            height=220,
            margin=dict(l=10, r=10, t=40, b=10),
            paper_bgcolor="#0D1117",
            plot_bgcolor="#0D1117",
            font=dict(color="#F0F6FC"),
            xaxis=dict(gridcolor="#21262D"),
            yaxis=dict(gridcolor="#21262D"),
        )
        st.plotly_chart(fig_dd, use_container_width=True)

    st.markdown("<div class='section-h'>Trades</div>", unsafe_allow_html=True)
    if bot_trades.empty:
        st.caption("No trades for this bot yet.")
    else:
        st.dataframe(
            bot_trades.sort_values("ts", ascending=False).head(200),
            use_container_width=True,
        )


# --- leaderboard / positions / signals / trades ---------------------------
def _render_leaderboard(eq_df: pd.DataFrame, trades_df: pd.DataFrame) -> None:
    if eq_df.empty:
        st.info("Leaderboard fills in once at least one bot has completed a cycle.")
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
    st.markdown("<div class='section-h'>Performance comparison</div>", unsafe_allow_html=True)
    st.dataframe(df.style.format(precision=2), use_container_width=True)

    st.markdown("<div class='section-h'>Strategy correlation</div>", unsafe_allow_html=True)
    corr = metrics.correlation_matrix(eq_by_strategy)
    if not corr.empty:
        fig = px.imshow(
            corr,
            text_auto=".2f",
            aspect="auto",
            color_continuous_scale="RdBu_r",
            zmin=-1,
            zmax=1,
        )
        fig.update_layout(
            height=360,
            margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="#0D1117",
            font=dict(color="#F0F6FC"),
        )
        st.plotly_chart(fig, use_container_width=True)


def _render_positions(positions: pd.DataFrame) -> None:
    if positions.empty:
        st.info("No open positions.")
        return
    if "error" in positions.columns:
        st.error(f"Could not fetch positions: {positions.iloc[0]['error']}")
        return
    st.markdown("<div class='section-h'>Open positions (live from broker)</div>", unsafe_allow_html=True)
    st.dataframe(
        positions.style.format(
            {
                "qty": "{:.4f}",
                "avg_entry_price": "${:.2f}",
                "market_value": "${:,.2f}",
                "unrealized_pl": "${:,.2f}",
                "unrealized_plpc_%": "{:.2f}%",
            }
        ).background_gradient(subset=["unrealized_pl"], cmap="RdYlGn"),
        use_container_width=True,
    )
    cols = st.columns(2)
    cols[0].markdown(
        _tile("Total market value", f"${positions['market_value'].sum():,.2f}"),
        unsafe_allow_html=True,
    )
    pl = positions["unrealized_pl"].sum()
    cols[1].markdown(
        _tile(
            "Unrealized P/L",
            f"${pl:,.2f}",
            "+" if pl >= 0 else "−",
            "pos" if pl >= 0 else "neg",
        ),
        unsafe_allow_html=True,
    )


def _render_signals(signals: pd.DataFrame) -> None:
    if signals.empty:
        st.info("No signals logged yet — bots haven't completed a cycle.")
        return
    st.dataframe(
        signals.sort_values("ts", ascending=False).head(200),
        use_container_width=True,
    )


def _render_trades(trades: pd.DataFrame) -> None:
    if trades.empty:
        st.info("No trades yet.")
        return
    st.dataframe(
        trades.sort_values("ts", ascending=False).head(200),
        use_container_width=True,
    )


_STATUS_COLORS = {
    "filled": "#3FB950",
    "accepted": "#58A6FF",
    "new": "#8B949E",
    "partially_filled": "#D29922",
    "canceled": "#8B949E",
    "rejected": "#F85149",
    "expired": "#F85149",
}


def _render_orders(orders: pd.DataFrame) -> None:
    if orders.empty:
        st.info("No orders submitted yet.")
        return
    st.markdown("<div class='section-h'>Most recent orders</div>", unsafe_allow_html=True)

    # Status breakdown.
    counts = orders["status"].value_counts().to_dict()
    cols = st.columns(min(len(counts), 6) or 1)
    for i, (status, n) in enumerate(counts.items()):
        color = _STATUS_COLORS.get(status, "#8B949E")
        cols[i % len(cols)].markdown(
            f"""
            <div class="tile" style="border-left:4px solid {color}">
              <div class="label">{status}</div>
              <div class="value">{n}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.dataframe(
        orders.sort_values("ts", ascending=False).head(200).style.format(
            {
                "qty": "{:.4f}",
                "filled_qty": "{:.4f}",
                "filled_avg_price": "${:.2f}",
            }
        ),
        use_container_width=True,
    )


def _render_bot_positions(bot_positions: pd.DataFrame) -> None:
    if bot_positions.empty:
        st.info("No positions tracked in the per-bot ledger yet.")
        return
    st.markdown("<div class='section-h'>Per-bot position ledger</div>", unsafe_allow_html=True)
    st.caption(
        "This is our internal attribution. Each row is a (bot, symbol) pair. "
        "Compare with the broker's positions on the Positions tab."
    )
    st.dataframe(
        bot_positions.sort_values(["strategy_id", "symbol"]).style.format(
            {
                "qty": "{:.4f}",
                "avg_price": "${:.2f}",
                "cost_basis": "${:,.2f}",
            }
        ),
        use_container_width=True,
    )


_SEVERITY_BG = {
    "info": "#1F6FEB22",
    "warning": "#D2992422",
    "error": "#F8514922",
    "critical": "#A81E1E55",
}


def _render_audit(events: pd.DataFrame) -> None:
    if events.empty:
        st.info("No audit events yet.")
        return
    st.markdown("<div class='section-h'>Most recent events (last 200)</div>", unsafe_allow_html=True)

    # Severity breakdown.
    counts = events["severity"].value_counts().to_dict()
    cols = st.columns(min(len(counts), 4) or 1)
    for i, (sev, n) in enumerate(counts.items()):
        cols[i % len(cols)].markdown(
            f"""
            <div class="tile" style="background:{_SEVERITY_BG.get(sev, '#161B22')}">
              <div class="label">{sev}</div>
              <div class="value">{n}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.dataframe(events, use_container_width=True, height=500)


def _render_bot_status(bot_status: pd.DataFrame) -> None:
    st.markdown("<div class='section-h'>Bot operational state</div>", unsafe_allow_html=True)
    if bot_status.empty:
        st.info("No bot_status rows yet — they're created by the orchestrator on first cycle.")
        st.caption(
            "Use the CLI to manage state:\n\n"
            "```\npython -m src.cli pause   --strategy momentum --reason \"manual hold\"\n"
            "python -m src.cli enable  --strategy momentum\n"
            "python -m src.cli graduate --strategy momentum\n```"
        )
        return
    cols = st.columns(min(len(bot_status), 3) or 1)
    for i, row in bot_status.iterrows():
        state_color = {
            "enabled": "#3FB950",
            "paused": "#D29922",
            "disabled": "#F85149",
        }.get(row["state"], "#8B949E")
        validated = (
            row["paper_validated_at"].strftime("%Y-%m-%d") if row["paper_validated_at"] else "no"
        )
        cols[i % len(cols)].markdown(
            f"""
            <div class="card" style="border-left:4px solid {state_color}">
              <h3>{row['strategy_id']}</h3>
              <div class="muted">state: <b style="color:{state_color}">{row['state']}</b></div>
              <div class="muted">paper-validated: {validated}</div>
              <hr style="border-color:#21262D;margin:10px 0">
              <div style="font-size:0.85em">{row['reason'] or '—'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.caption(
        "Pause / enable / graduate from the CLI:  `python -m src.cli status`"
    )


# --- main -----------------------------------------------------------------
def main() -> None:
    if not _password_gate():
        st.stop()
    st.markdown(_CSS, unsafe_allow_html=True)

    settings = get_settings()
    _render_header(settings)
    account = _load_account()
    _render_status_tiles(account)

    eq_df = _load_equity()
    trades_df = _load_trades()
    signals_df = _load_signals()
    positions_df = _load_positions()
    orders_df = _load_orders()
    bot_positions_df = _load_bot_positions()
    audit_df = _load_audit()
    bot_status_df = _load_bot_status()

    strategies_with_data = sorted(eq_df["strategy_id"].unique()) if not eq_df.empty else []
    tab_names = [
        "Overview",
        "Leaderboard",
        "Bots",
        "Positions",
        "Orders",
        "Trades",
        "Signals",
        "Audit",
    ]
    tab_names += strategies_with_data
    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_account_chart(account)
        _render_risk_caps(settings)
        _render_bot_cards(trades_df, signals_df)
    with tabs[1]:
        _render_leaderboard(eq_df, trades_df)
    with tabs[2]:
        _render_bot_status(bot_status_df)
        st.divider()
        _render_bot_positions(bot_positions_df)
    with tabs[3]:
        _render_positions(positions_df)
    with tabs[4]:
        _render_orders(orders_df)
    with tabs[5]:
        _render_trades(trades_df)
    with tabs[6]:
        _render_signals(signals_df)
    with tabs[7]:
        _render_audit(audit_df)
    for sid, tab in zip(strategies_with_data, tabs[8:]):
        with tab:
            st.header(sid)
            _render_bot_tab(sid, eq_df, trades_df)


if __name__ == "__main__":
    main()
