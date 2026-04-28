"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Pill, statusTone } from "@/components/ui/pill";
import { Metric } from "@/components/ui/metric";
import { EmptyState, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { fmtNum, fmtUSD } from "@/lib/utils";

export default function OrdersPage() {
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => api.orders(200) });

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const o of orders.data ?? []) c[o.status] = (c[o.status] ?? 0) + 1;
    return c;
  }, [orders.data]);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Metric label="Total" value={orders.data?.length ?? 0} />
        {["filled", "accepted", "partially_filled", "rejected"].map((s) => (
          <Metric key={s} label={s.replace("_", " ")} value={counts[s] ?? 0} />
        ))}
      </div>

      <Card>
        <CardHeader title="Recent orders" subtitle="Latest 200, newest first." />
        <CardBody>
          {!orders.data || orders.data.length === 0 ? (
            <EmptyState>No orders submitted yet.</EmptyState>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Strategy</TH>
                  <TH>Symbol</TH>
                  <TH>Side</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Filled</TH>
                  <TH className="text-right">Avg price</TH>
                  <TH>Status</TH>
                </TR>
              </THead>
              <tbody>
                {orders.data.map((o) => (
                  <TR key={o.id}>
                    <TD className="text-[var(--color-text-muted)] text-xs">
                      {new Date(o.ts).toLocaleString()}
                    </TD>
                    <TD>
                      <code className="text-xs">{o.strategy_id}</code>
                    </TD>
                    <TD className="font-medium">{o.symbol}</TD>
                    <TD className="capitalize">{o.side}</TD>
                    <TD num>{fmtNum(o.qty, 4)}</TD>
                    <TD num>{fmtNum(o.filled_qty, 4)}</TD>
                    <TD num>
                      {o.filled_avg_price > 0 ? fmtUSD(o.filled_avg_price) : "—"}
                    </TD>
                    <TD>
                      <Pill tone={statusTone(o.status)}>{o.status}</Pill>
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
