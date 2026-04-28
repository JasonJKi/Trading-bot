// Mirrors src/api/schemas.py. Keep these in sync.
export type Health = { status: string; ts: string; mode: "PAPER" | "LIVE" };

export type Account = {
  equity: number;
  last_equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  status: string;
  delta: number;
  delta_pct: number;
} | null;

export type RiskCaps = {
  per_bot_cap: number;
  per_position_pct: number;
  global_max_drawdown: number;
  per_bot_max_drawdown: number;
  starting_equity: number;
};

export type BotInfo = {
  id: string;
  name: string;
  version: string;
  schedule: Record<string, string | number>;
  universe: string[];
  state: "enabled" | "paused" | "disabled" | string;
  reason: string;
  paper_validated_at: string | null;
  next_run: string | null;
  n_signals: number;
  n_trades: number;
};

export type Regime = {
  regime: "bull" | "bear" | "chop" | "crisis" | string;
  spy_trend_pct: number;
  vix: number;
  term_structure: number;
  breadth: number;
  correlation: number;
  ts: string;
};

export type Position = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  side: string;
};

export type BotPosition = {
  strategy_id: string;
  symbol: string;
  qty: number;
  avg_price: number;
  cost_basis: number;
  opened_at: string;
  updated_at: string;
};

export type Order = {
  id: number;
  ts: string;
  strategy_id: string;
  symbol: string;
  side: string;
  qty: number;
  status: string;
  filled_qty: number;
  filled_avg_price: number;
  client_order_id: string;
  broker_order_id: string;
  error: string;
};

export type Trade = {
  id: number;
  ts: string;
  strategy_id: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  notional: number;
  order_id: string;
};

export type Signal = {
  id: number;
  ts: string;
  strategy_id: string;
  symbol: string;
  direction: string;
  strength: number;
  acted: number;
};

export type EquityPoint = {
  ts: string;
  strategy_id: string;
  cash: number;
  position_value: number;
  total_equity: number;
};

export type PerformanceRow = {
  strategy_id: string;
  total_return: number;
  cagr: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  win_rate: number;
  expectancy: number;
};

export type AuditRow = {
  id: number;
  ts: string;
  kind: string;
  severity: "info" | "warning" | "error" | "critical" | string;
  strategy_id: string;
  message: string;
};
