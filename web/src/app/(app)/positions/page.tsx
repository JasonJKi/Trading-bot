"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { EmptyState, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { deltaTone, fmtNum, fmtPct, fmtUSD } from "@/lib/utils";

export default function PositionsPage() {
  const positions = useQuery({ queryKey: ["positions"], queryFn: api.positions });
  const ledger = useQuery({ queryKey: ["botPositions"], queryFn: api.botPositions });

  const totalMV = positions.data?.reduce((s, p) => s + p.market_value, 0) ?? 0;
  const totalPL = positions.data?.reduce((s, p) => s + p.unrealized_pl, 0) ?? 0;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Metric label="Open positions" value={positions.data?.length ?? 0} />
        <Metric label="Total market value" value={fmtUSD(totalMV)} />
        <Metric
          label="Unrealized P/L"
          value={fmtUSD(totalPL)}
          deltaTone={deltaTone(totalPL)}
          delta={fmtUSD(totalPL, { sign: true })}
        />
        <Metric label="Bot ledger rows" value={ledger.data?.length ?? 0} />
      </div>

      <Card>
        <CardHeader title="Open positions (live from broker)" />
        <CardBody>
          {!positions.data || positions.data.length === 0 ? (
            <EmptyState>No open positions.</EmptyState>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Symbol</TH>
                  <TH className="text-right">Side</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Avg entry</TH>
                  <TH className="text-right">Market value</TH>
                  <TH className="text-right">Unrealized P/L</TH>
                  <TH className="text-right">%</TH>
                </TR>
              </THead>
              <tbody>
                {positions.data.map((p) => (
                  <TR key={p.symbol}>
                    <TD className="font-medium">{p.symbol}</TD>
                    <TD align="right" className="capitalize">{p.side}</TD>
                    <TD num>{fmtNum(p.qty, 4)}</TD>
                    <TD num>{fmtUSD(p.avg_entry_price)}</TD>
                    <TD num>{fmtUSD(p.market_value)}</TD>
                    <TD
                      num
                      className={
                        p.unrealized_pl > 0
                          ? "text-[var(--color-positive)]"
                          : p.unrealized_pl < 0
                          ? "text-[var(--color-negative)]"
                          : ""
                      }
                    >
                      {fmtUSD(p.unrealized_pl, { sign: true })}
                    </TD>
                    <TD
                      num
                      className={
                        p.unrealized_plpc > 0
                          ? "text-[var(--color-positive)]"
                          : p.unrealized_plpc < 0
                          ? "text-[var(--color-negative)]"
                          : ""
                      }
                    >
                      {fmtPct(p.unrealized_plpc, 2, true)}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Per-bot position ledger"
          subtitle="Internal attribution. The broker reports one position per symbol — this splits it by bot."
        />
        <CardBody>
          {!ledger.data || ledger.data.length === 0 ? (
            <EmptyState>No positions tracked yet.</EmptyState>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Strategy</TH>
                  <TH>Symbol</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Avg price</TH>
                  <TH className="text-right">Cost basis</TH>
                  <TH>Updated</TH>
                </TR>
              </THead>
              <tbody>
                {ledger.data.map((p, i) => (
                  <TR key={`${p.strategy_id}-${p.symbol}-${i}`}>
                    <TD>
                      <code className="text-xs">{p.strategy_id}</code>
                    </TD>
                    <TD className="font-medium">{p.symbol}</TD>
                    <TD num>{fmtNum(p.qty, 4)}</TD>
                    <TD num>{fmtUSD(p.avg_price)}</TD>
                    <TD num>{fmtUSD(p.cost_basis)}</TD>
                    <TD className="text-[var(--color-text-muted)] text-xs">
                      {new Date(p.updated_at).toLocaleString()}
                    </TD>
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
