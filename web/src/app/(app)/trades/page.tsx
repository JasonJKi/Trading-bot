"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { fmtNum, fmtUSD } from "@/lib/utils";

export default function TradesPage() {
  const trades = useQuery({ queryKey: ["trades"], queryFn: () => api.trades(200) });

  return (
    <Card>
      <CardHeader title="Trades" subtitle="Filled or partially filled. Latest 200." />
      <CardBody>
        {!trades.data || trades.data.length === 0 ? (
          <EmptyState>No trades yet.</EmptyState>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>When</TH>
                <TH>Strategy</TH>
                <TH>Symbol</TH>
                <TH>Side</TH>
                <TH className="text-right">Qty</TH>
                <TH className="text-right">Price</TH>
                <TH className="text-right">Notional</TH>
              </TR>
            </THead>
            <tbody>
              {trades.data.map((t) => (
                <TR key={t.id}>
                  <TD className="text-[var(--color-text-muted)] text-xs">
                    {new Date(t.ts).toLocaleString()}
                  </TD>
                  <TD>
                    <code className="text-xs">{t.strategy_id}</code>
                  </TD>
                  <TD className="font-medium">{t.symbol}</TD>
                  <TD
                    className={
                      t.side === "buy"
                        ? "text-[var(--color-positive)] capitalize"
                        : "text-[var(--color-negative)] capitalize"
                    }
                  >
                    {t.side}
                  </TD>
                  <TD num>{fmtNum(t.qty, 4)}</TD>
                  <TD num>{fmtUSD(t.price)}</TD>
                  <TD num>{fmtUSD(t.notional)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
