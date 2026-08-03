"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
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
  type Finding,
  type TimelineEntry,
  fetchAuditHistory,
  fetchKillSwitch,
  fetchLatestAudit,
  liveAuditSocketUrl,
  parseTimelineEntry,
  setKillSwitch,
} from "@/lib/api";

const DEFAULT_REPO = "E:/Abrar/AI_ML/Sentinel";

const RISK_TIER_VARIANT: Record<string, "destructive" | "secondary"> = {
  risky: "destructive",
  mechanical: "secondary",
};

const PR_STATUS_LABEL: Record<string, string> = {
  not_auto_fixable: "Not auto-fixable",
  validation_failed: "Fix failed validation",
  duplicate_skipped: "Already handled",
  opened: "PR opened",
  opened_draft: "Draft PR (needs review)",
  halted: "Halted by kill switch",
};

function PrStatusBadge({ finding }: { finding: Finding }) {
  const status = finding.pr_status;
  if (!status) return <span className="text-muted-foreground">—</span>;
  const variant =
    status === "opened" || status === "opened_draft"
      ? "default"
      : status === "validation_failed" || status === "halted"
        ? "destructive"
        : "secondary";
  return <Badge variant={variant}>{PR_STATUS_LABEL[status] ?? status}</Badge>;
}

function FixDiff({ finding }: { finding: Finding }) {
  const fix = finding.proposed_fix;
  const val = finding.validation;
  if (!fix) return null;

  return (
    <div className="bg-muted/30 space-y-3 rounded-md border p-4 text-sm">
      <div>
        <p className="text-muted-foreground mb-1 text-xs font-medium uppercase">
          Proposed fix
        </p>
        <p className="mb-2">{fix.explanation}</p>
        <div className="overflow-x-auto rounded font-mono text-xs">
          {fix.old_snippet && (
            <div className="bg-destructive/10 text-destructive whitespace-pre-wrap px-2 py-1">
              − {fix.old_snippet}
            </div>
          )}
          {fix.new_snippet && (
            <div className="whitespace-pre-wrap bg-emerald-500/10 px-2 py-1 text-emerald-700 dark:text-emerald-400">
              + {fix.new_snippet}
            </div>
          )}
        </div>
      </div>

      {val && (
        <div>
          <p className="text-muted-foreground mb-1 text-xs font-medium uppercase">
            Validation
          </p>
          <div className="mb-1 flex gap-2">
            <Badge variant={val.lint_passed ? "secondary" : "destructive"}>
              lint {val.lint_passed ? "passed" : "failed"}
            </Badge>
            <Badge variant={val.tests_passed ? "secondary" : "destructive"}>
              tests {val.tests_passed ? "passed" : "failed"}
            </Badge>
          </div>
        </div>
      )}

      {finding.pr_url && (
        <a
          href={finding.pr_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary inline-block text-sm font-medium underline underline-offset-2"
        >
          View PR on GitHub →
        </a>
      )}
    </div>
  );
}

type HistoryPoint = {
  label: string;
  findings: number;
  costCents: number;
};

function AuditHistoryChart({ history }: { history: AuditResult[] }) {
  if (history.length < 2) {
    return (
      <p className="text-muted-foreground text-sm">
        Run at least two audits to see a trend line.
      </p>
    );
  }

  const data: HistoryPoint[] = history.map((a, i) => ({
    label: a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : `#${i + 1}`,
    findings: a.findings.length,
    costCents: Math.round((a.cost_usd ?? 0) * 100),
  }));

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis dataKey="label" fontSize={11} tickLine={false} />
          <YAxis fontSize={11} tickLine={false} allowDecimals={false} />
          <Tooltip
            contentStyle={{
              fontSize: 12,
              borderRadius: 8,
              background: "var(--popover)",
              color: "var(--popover-foreground)",
              border: "1px solid var(--border)",
            }}
          />
          <Line
            type="monotone"
            dataKey="findings"
            name="Findings"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="costCents"
            name="Cost (¢)"
            stroke="#10b981"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function Home() {
  const [repo, setRepo] = useState(DEFAULT_REPO);
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [killActive, setKillActive] = useState(false);
  const [killBusy, setKillBusy] = useState(false);
  const [history, setHistory] = useState<AuditResult[]>([]);
  const seqRef = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);

  function refreshHistory() {
    fetchAuditHistory()
      .then(setHistory)
      .catch(() => {});
  }

  useEffect(() => {
    fetchLatestAudit()
      .then(setAudit)
      .catch((err) => setError(String(err)));
    fetchKillSwitch()
      .then(setKillActive)
      .catch(() => {});
    refreshHistory();
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [timeline]);

  function handleRunAudit() {
    setRunning(true);
    setError(null);
    setTimeline([]);
    setExpanded(new Set());
    seqRef.current = 0;

    const ws = new WebSocket(liveAuditSocketUrl(repo));

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
            cost_usd: parsed.cost_usd,
            timestamp: parsed.timestamp,
            error: parsed.error,
          });
          setRunning(false);
          refreshHistory();
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

  async function handleToggleKillSwitch() {
    setKillBusy(true);
    try {
      const next = await setKillSwitch(!killActive);
      setKillActive(next);
    } catch (err) {
      setError(String(err));
    } finally {
      setKillBusy(false);
    }
  }

  function toggleExpanded(i: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sentinel</h1>
          <p className="text-muted-foreground text-sm">
            Autonomous codebase auditor — Security, Quality &amp; Test analysts
          </p>
        </div>
        <Button
          onClick={handleToggleKillSwitch}
          disabled={killBusy}
          variant={killActive ? "destructive" : "outline"}
        >
          {killActive ? "🛑 Kill switch: HALTED" : "Kill switch: active"}
        </Button>
      </div>

      <div className="flex items-center gap-2">
        <Input
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="Path to target repository"
          className="font-mono text-sm"
        />
        <Button onClick={handleRunAudit} disabled={running || killActive}>
          {running ? "Running audit…" : "Run audit"}
        </Button>
      </div>

      {killActive && (
        <p className="text-destructive text-sm">
          Kill switch is active — no new audits or agent actions will run until deactivated.
        </p>
      )}
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
                    <TableHead>Symbol</TableHead>
                    <TableHead>Explanation</TableHead>
                    <TableHead>Fix</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.findings.map((f, i) => (
                    <Fragment key={i}>
                      <TableRow
                        className={f.proposed_fix ? "cursor-pointer" : undefined}
                        onClick={() => f.proposed_fix && toggleExpanded(i)}
                      >
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
                        <TableCell>
                          <Badge variant="secondary">{f.symbol || "—"}</Badge>
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          {f.explanation}
                        </TableCell>
                        <TableCell>
                          <PrStatusBadge finding={f} />
                        </TableCell>
                      </TableRow>
                      {expanded.has(i) && f.proposed_fix && (
                        <TableRow>
                          <TableCell colSpan={6}>
                            <FixDiff finding={f} />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Audit history</CardTitle>
            <CardDescription>
              Findings and cost per audit this session ({history.length} run
              {history.length === 1 ? "" : "s"})
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AuditHistoryChart history={history} />
          </CardContent>
        </Card>
      )}

      <Separator />
      <p className="text-muted-foreground text-center text-xs">
        Sentinel never merges anything automatically. Every PR — mechanical or risky — is
        opened for human review; risky fixes open as drafts and are always labeled
        needs-security-review.
      </p>
    </div>
  );
}
