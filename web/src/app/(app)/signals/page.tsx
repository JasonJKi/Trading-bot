"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, TD, TH, THead, TR, Table } from "@/components/ui/table";

export default function SignalsPage() {
  const signals = useQuery({ queryKey: ["signals"], queryFn: () => api.signals(200) });

  return (
    <Card>
      <CardHeader title="Signals" subtitle="What each bot saw. Acted = 1 if it triggered an order." />
      <CardBody>
        {!signals.data || signals.data.length === 0 ? (
          <EmptyState>No signals logged yet.</EmptyState>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>When</TH>
                <TH>Strategy</TH>
                <TH>Symbol</TH>
                <TH>Direction</TH>
                <TH className="text-right">Strength</TH>
                <TH>Acted</TH>
              </TR>
            </THead>
            <tbody>
              {signals.data.map((s) => (
                <TR key={s.id}>
                  <TD className="text-[var(--color-text-muted)] text-xs">
                    {new Date(s.ts).toLocaleString()}
                  </TD>
                  <TD>
                    <code className="text-xs">{s.strategy_id}</code>
                  </TD>
                  <TD className="font-medium">{s.symbol}</TD>
                  <TD className="capitalize">{s.direction}</TD>
                  <TD num>{s.strength.toFixed(3)}</TD>
                  <TD>{s.acted ? "✓" : "—"}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
