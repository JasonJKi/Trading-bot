"use client";

import { EquityPoint } from "@/lib/types";
import { botColorVar, fmtUSD } from "@/lib/utils";
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function EquityChart({ points }: { points: EquityPoint[] }) {
  const { rows, strategies } = useMemo(() => {
    const strategiesSet = new Set<string>();
    const byTs = new Map<string, Record<string, number | string>>();
    for (const p of points) {
      strategiesSet.add(p.strategy_id);
      const key = new Date(p.ts).toISOString();
      const row = byTs.get(key) ?? { ts: key };
      row[p.strategy_id] = p.total_equity;
      byTs.set(key, row);
    }
    const rows = Array.from(byTs.values()).sort((a, b) =>
      String(a.ts).localeCompare(String(b.ts))
    );
    return { rows, strategies: Array.from(strategiesSet) };
  }, [points]);

  if (rows.length < 2) {
    return (
      <div className="h-72 flex items-center justify-center text-sm text-[var(--color-text-muted)]">
        Not enough equity history yet — fills in once bots have completed a few cycles.
      </div>
    );
  }

  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 12 }}>
          <defs>
            {strategies.map((s) => {
              const c = botColorVar(s);
              return (
                <linearGradient key={s} id={`g-${s}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={c} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={c} stopOpacity={0} />
                </linearGradient>
              );
            })}
          </defs>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="ts"
            tick={{ fill: "var(--color-text-muted)", fontSize: 11 }}
            axisLine={{ stroke: "var(--color-border)" }}
            tickLine={false}
            tickFormatter={(v: string) => {
              const d = new Date(v);
              return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(
                d.getMinutes()
              ).padStart(2, "0")}`;
            }}
          />
          <YAxis
            tick={{ fill: "var(--color-text-muted)", fontSize: 11 }}
            axisLine={{ stroke: "var(--color-border)" }}
            tickLine={false}
            tickFormatter={(v) => fmtUSD(v as number, { compact: true })}
            domain={["auto", "auto"]}
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border-strong)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelFormatter={(v) => new Date(v as string).toLocaleString()}
            formatter={(v, name) => [fmtUSD(Number(v)), String(name)]}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "var(--color-text-muted)" }}
            iconType="circle"
          />
          {strategies.map((s) => (
            <Area
              key={s}
              type="monotone"
              dataKey={s}
              stroke={botColorVar(s)}
              strokeWidth={1.8}
              fill={`url(#g-${s})`}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
