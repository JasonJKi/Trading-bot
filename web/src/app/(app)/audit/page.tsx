"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Pill, severityTone } from "@/components/ui/pill";
import { Metric } from "@/components/ui/metric";
import { EmptyState, TD, TH, THead, TR, Table } from "@/components/ui/table";

export default function AuditPage() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit(200) });

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of audit.data ?? []) c[e.severity] = (c[e.severity] ?? 0) + 1;
    return c;
  }, [audit.data]);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {(["info", "warning", "error", "critical"] as const).map((s) => (
          <Metric key={s} label={s} value={counts[s] ?? 0} />
        ))}
      </div>

      <Card>
        <CardHeader title="Audit log" subtitle="Append-only — what the bot did and why." />
        <CardBody>
          {!audit.data || audit.data.length === 0 ? (
            <EmptyState>No audit events yet.</EmptyState>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Severity</TH>
                  <TH>Kind</TH>
                  <TH>Strategy</TH>
                  <TH>Message</TH>
                </TR>
              </THead>
              <tbody>
                {audit.data.map((e) => (
                  <TR key={e.id}>
                    <TD className="text-[var(--color-text-muted)] text-xs">
                      {new Date(e.ts).toLocaleString()}
                    </TD>
                    <TD>
                      <Pill tone={severityTone(e.severity)}>{e.severity}</Pill>
                    </TD>
                    <TD>
                      <code className="text-xs">{e.kind}</code>
                    </TD>
                    <TD>
                      <code className="text-xs text-[var(--color-text-muted)]">
                        {e.strategy_id || "—"}
                      </code>
                    </TD>
                    <TD className="text-xs">{e.message}</TD>
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
