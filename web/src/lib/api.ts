// Thin client. All requests are relative ("/api/...") and proxied to FastAPI
// by next.config.ts -> rewrites().
async function http<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 401) {
    throw Object.assign(new Error("not authenticated"), { code: 401 });
  }
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => http<import("./types").Health>("/api/health"),
  authStatus: () => http<{ required: boolean }>("/api/auth/status"),
  login: (password: string) =>
    http<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () => http<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  account: () => http<import("./types").Account>("/api/account"),
  riskCaps: () => http<import("./types").RiskCaps>("/api/risk-caps"),
  bots: () => http<import("./types").BotInfo[]>("/api/bots"),
  regime: () => http<import("./types").Regime>("/api/regime"),
  positions: () => http<import("./types").Position[]>("/api/positions"),
  botPositions: () => http<import("./types").BotPosition[]>("/api/bot-positions"),
  orders: (limit = 200) => http<import("./types").Order[]>(`/api/orders?limit=${limit}`),
  trades: (limit = 200) => http<import("./types").Trade[]>(`/api/trades?limit=${limit}`),
  signals: (limit = 200) => http<import("./types").Signal[]>(`/api/signals?limit=${limit}`),
  equity: () => http<import("./types").EquityPoint[]>("/api/equity"),
  performance: () => http<import("./types").PerformanceRow[]>("/api/performance"),
  audit: (limit = 200) => http<import("./types").AuditRow[]>(`/api/audit?limit=${limit}`),
};
