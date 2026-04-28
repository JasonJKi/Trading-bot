"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader, SectionHeader } from "@/components/ui/card";
import { Pill, botStateTone } from "@/components/ui/pill";
import { EmptyState, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { botColorVar, fmtPct, fmtUSD, relativeTime } from "@/lib/utils";

export default function BotsPage() {
  const bots = useQuery({ queryKey: ["bots"], queryFn: api.bots });
  const perf = useQuery({ queryKey: ["performance"], queryFn: api.performance });

  return (
    <div className="space-y-5">
      <SectionHeader>Operational state</SectionHeader>
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
                <code className="text-[var(--color-text-subtle)] text-xs">
                  {b.id} · v{b.version}
                </code>
              }
            />
            <CardBody>
              <dl className="grid grid-cols-2 gap-y-2 text-sm">
                <dt className="text-[var(--color-text-muted)]">Paper-validated</dt>
                <dd className="text-right">
                  {b.paper_validated_at
                    ? new Date(b.paper_validated_at).toLocaleDateString()
                    : "no"}
                </dd>
                <dt className="text-[var(--color-text-muted)]">Next run</dt>
                <dd className="text-right num">{relativeTime(b.next_run)}</dd>
                <dt className="text-[var(--color-text-muted)]">Schedule</dt>
                <dd className="text-right text-xs text-[var(--color-text-muted)] font-mono">
                  {b.schedule.day_of_week} {b.schedule.hour}:{b.schedule.minute} UTC
                </dd>
              </dl>
              {b.reason && (
                <div className="mt-3 text-xs text-[var(--color-text-muted)] border-t border-[var(--color-border)] pt-2">
                  {b.reason}
                </div>
              )}
            </CardBody>
          </Card>
        ))}
      </div>

      <SectionHeader>Performance comparison</SectionHeader>
      <Card>
        <CardBody>
          {!perf.data || perf.data.length === 0 ? (
            <EmptyState>
              Performance fills in once at least one bot has logged a few equity snapshots.
            </EmptyState>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Strategy</TH>
                  <TH className="text-right">Total return</TH>
                  <TH className="text-right">CAGR</TH>
                  <TH className="text-right">Sharpe</TH>
                  <TH className="text-right">Sortino</TH>
                  <TH className="text-right">Max DD</TH>
                  <TH className="text-right">Win rate</TH>
                  <TH className="text-right">Expectancy</TH>
                </TR>
              </THead>
              <tbody>
                {perf.data.map((r) => (
                  <TR key={r.strategy_id}>
                    <TD>
                      <code className="text-xs">{r.strategy_id}</code>
                    </TD>
                    <TD num>{fmtPct(r.total_return * 100, 2, true)}</TD>
                    <TD num>{fmtPct(r.cagr * 100, 2, true)}</TD>
                    <TD num>{r.sharpe.toFixed(2)}</TD>
                    <TD num>{r.sortino.toFixed(2)}</TD>
                    <TD num className="text-[var(--color-negative)]">
                      {fmtPct(r.max_drawdown * 100)}
                    </TD>
                    <TD num>{fmtPct(r.win_rate * 100, 1)}</TD>
                    <TD num>{fmtUSD(r.expectancy)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
