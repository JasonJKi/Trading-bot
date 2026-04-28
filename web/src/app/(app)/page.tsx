"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader, SectionHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { Pill, regimeTone, botStateTone } from "@/components/ui/pill";
import { EquityChart } from "@/components/charts/equity-chart";
import { botColorVar, deltaTone, fmtPct, fmtUSD, relativeTime } from "@/lib/utils";

export default function OverviewPage() {
  const account = useQuery({ queryKey: ["account"], queryFn: api.account });
  const risk = useQuery({ queryKey: ["risk"], queryFn: api.riskCaps });
  const bots = useQuery({ queryKey: ["bots"], queryFn: api.bots });
  const regime = useQuery({ queryKey: ["regime"], queryFn: api.regime });
  const equity = useQuery({ queryKey: ["equity"], queryFn: api.equity });

  return (
    <div className="space-y-5">
      {/* Account row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Metric
          label="Account equity"
          loading={account.isLoading}
          value={account.data ? fmtUSD(account.data.equity) : "—"}
          delta={
            account.data
              ? `${fmtUSD(account.data.delta, { sign: true })} (${fmtPct(account.data.delta_pct, 2, true)})`
              : "no creds"
          }
          deltaTone={account.data ? deltaTone(account.data.delta) : "neutral"}
        />
        <Metric
          label="Cash"
          loading={account.isLoading}
          value={account.data ? fmtUSD(account.data.cash) : "—"}
        />
        <Metric
          label="Buying power"
          loading={account.isLoading}
          value={account.data ? fmtUSD(account.data.buying_power) : "—"}
        />
        <Metric
          label="Account status"
          loading={account.isLoading}
          value={
            <span className="text-base capitalize">
              {account.data?.status?.replace(/^AccountStatus\./, "").toLowerCase() ?? "offline"}
            </span>
          }
        />
      </div>

      {/* Equity chart */}
      <Card>
        <CardHeader
          title="Equity over time"
          subtitle="Per-bot total equity, sampled at the end of each cycle."
        />
        <CardBody>
          <EquityChart points={equity.data ?? []} />
        </CardBody>
      </Card>

      {/* Regime + risk caps */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Card className="lg:col-span-1">
          <CardHeader
            title="Market regime"
            right={
              regime.data && (
                <Pill tone={regimeTone(regime.data.regime)}>{regime.data.regime}</Pill>
              )
            }
          />
          <CardBody>
            {regime.isLoading || !regime.data ? (
              <div className="text-sm text-[var(--color-text-muted)]">…</div>
            ) : (
              <dl className="grid grid-cols-2 gap-y-2 gap-x-4 text-sm">
                <dt className="text-[var(--color-text-muted)]">SPY trend</dt>
                <dd className="num text-right">{fmtPct(regime.data.spy_trend_pct)}</dd>
                <dt className="text-[var(--color-text-muted)]">VIX</dt>
                <dd className="num text-right">{regime.data.vix.toFixed(2)}</dd>
                <dt className="text-[var(--color-text-muted)]">Term ratio</dt>
                <dd className="num text-right">{regime.data.term_structure.toFixed(2)}</dd>
                <dt className="text-[var(--color-text-muted)]">Breadth</dt>
                <dd className="num text-right">{(regime.data.breadth * 100).toFixed(1)}%</dd>
                <dt className="text-[var(--color-text-muted)]">Avg correlation</dt>
                <dd className="num text-right">{regime.data.correlation.toFixed(2)}</dd>
              </dl>
            )}
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Risk caps" subtitle="Hard limits enforced by the orchestrator." />
          <CardBody>
            {risk.data && (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <Metric label="Per-bot cap" value={fmtUSD(risk.data.per_bot_cap)} />
                <Metric
                  label="Per-position"
                  value={fmtPct(risk.data.per_position_pct * 100, 1)}
                />
                <Metric
                  label="Global DD halt"
                  value={fmtPct(risk.data.global_max_drawdown * 100, 0)}
                />
                <Metric
                  label="Per-bot DD halt"
                  value={fmtPct(risk.data.per_bot_max_drawdown * 100, 0)}
                />
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      {/* Bots */}
      <SectionHeader>Bots running</SectionHeader>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {(bots.data ?? []).map((b) => (
          <Card key={b.id} accent={botColorVar(b.id)}>
            <CardHeader
              title={
                <div className="flex items-center gap-2">
                  {b.name}
                  <Pill tone={botStateTone(b.state)}>{b.state}</Pill>
                </div>
              }
              subtitle={
                <code className="text-[var(--color-text-subtle)] text-xs">{b.id}</code>
              }
            />
            <CardBody className="space-y-3">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs">Next run</div>
                  <div className="font-medium num">{relativeTime(b.next_run)}</div>
                </div>
                <div className="text-right">
                  <div className="text-[var(--color-text-muted)] text-xs">Activity</div>
                  <div className="num">{b.n_signals} signals</div>
                  <div className="num">{b.n_trades} trades</div>
                </div>
              </div>
              <div className="border-t border-[var(--color-border)] pt-2">
                <div className="text-[var(--color-text-muted)] text-xs mb-1">
                  Universe ({b.universe.length})
                </div>
                <div className="text-xs text-[var(--color-text)] leading-relaxed">
                  {b.universe.slice(0, 12).join(", ")}
                  {b.universe.length > 12 && "…"}
                </div>
              </div>
            </CardBody>
          </Card>
        ))}
        {bots.data && bots.data.length === 0 && (
          <div className="text-sm text-[var(--color-text-muted)]">
            No bots enabled. Set <code>ENABLED_BOTS</code> in your <code>.env</code>.
          </div>
        )}
      </div>
    </div>
  );
}
