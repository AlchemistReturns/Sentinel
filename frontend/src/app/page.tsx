"use client";

import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  type AuditResult,
  type TimelineEntry,
  fetchLatestAudit,
  liveAuditSocketUrl,
  parseTimelineEntry,
} from "@/lib/api";

const DEFAULT_REPO = "E:/Abrar/AI_ML/Sentinel";

const RISK_TIER_VARIANT: Record<string, "destructive" | "secondary"> = {
  risky: "destructive",
  mechanical: "secondary",
};

export default function Home() {
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const seqRef = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchLatestAudit()
      .then(setAudit)
      .catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [timeline]);

  function handleRunAudit() {
    setRunning(true);
    setError(null);
    setTimeline([]);
    seqRef.current = 0;

    const ws = new WebSocket(liveAuditSocketUrl(DEFAULT_REPO));

    ws.onmessage = (ev) => {
      try {
        const entry = parseTimelineEntry(ev.data, seqRef.current++);
        if (entry) setTimeline((prev) => [...prev, entry]);

        const parsed = JSON.parse(ev.data);
        if (parsed.event === "done") {
          setAudit({
            audit_id: parsed.audit_id,
            repo: parsed.repo,
            findings: parsed.findings,
          });
          setRunning(false);
        }
      } catch {
        // ignore malformed frames
      }
    };

    ws.onerror = () => {
      setError("Live audit connection failed. Is the orchestrator running?");
      setRunning(false);
    };
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sentinel</h1>
          <p className="text-muted-foreground text-sm">
            Autonomous codebase auditor — Security, Quality &amp; Test analysts
          </p>
        </div>
        <Button onClick={handleRunAudit} disabled={running}>
          {running ? "Running audit…" : "Run audit"}
        </Button>
      </div>

      {error && <p className="text-destructive text-sm">Error: {error}</p>}

      {(running || timeline.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Live audit</CardTitle>
            <CardDescription>Sub-agent activity as it happens</CardDescription>
          </CardHeader>
          <CardContent>
            <div
              ref={logRef}
              className="bg-muted/50 max-h-64 overflow-y-auto rounded-md p-3 font-mono text-xs leading-relaxed"
            >
              {timeline.map((entry) => (
                <div key={entry.id}>
                  <span>{entry.label}</span>
                  {entry.detail && (
                    <span className="text-muted-foreground"> — {entry.detail}</span>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {!audit && !running && !error && (
        <p className="text-muted-foreground text-sm">
          No audits yet. Click &quot;Run audit&quot; to investigate the repository.
        </p>
      )}

      {audit && (
        <Card>
          <CardHeader>
            <CardTitle>Findings</CardTitle>
            <CardDescription>
              Audit {audit.audit_id} · {audit.repo}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {audit.findings.length === 0 ? (
              <p className="text-muted-foreground text-sm">No findings.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Analyst</TableHead>
                    <TableHead>Risk</TableHead>
                    <TableHead>File</TableHead>
                    <TableHead>Line</TableHead>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Explanation</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.findings.map((f, i) => (
                    <TableRow key={i}>
                      <TableCell className="capitalize">{f.analyst ?? "—"}</TableCell>
                      <TableCell>
                        {f.risk_tier ? (
                          <Badge variant={RISK_TIER_VARIANT[f.risk_tier] ?? "secondary"}>
                            {f.risk_tier}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{f.file_path}</TableCell>
                      <TableCell>{f.line}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{f.symbol || "—"}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {f.explanation}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
