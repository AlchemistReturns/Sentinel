"use client";

import {
  Bug,
  FlaskConical,
  GitPullRequest,
  Loader2,
  Play,
  Radio,
  ShieldCheck,
  Sparkles,
  Square,
} from "lucide-react";
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
import { ThemeToggle } from "@/components/theme-toggle";
import {
  type AuditResult,
  type Finding,
  type TimelineEntry,
  connectRepo,
  fetchAuditHistory,
  fetchKillSwitch,
  fetchLatestAudit,
  isGitHubUrl,
  liveAuditSocketUrl,
  parseTimelineEntry,
  setKillSwitch,
} from "@/lib/api";

const DEFAULT_REPO = "E:/Abrar/AI_ML/Sentinel";

const ANALYST_ICON: Record<string, React.ElementType> = {
  security: ShieldCheck,
  quality: Sparkles,
  test: FlaskConical,
};

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
    <div className="bg-muted/30 space-y-3 rounded-lg border p-4 text-sm">
      <div>
        <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
          Proposed fix
        </p>
        <p className="mb-2">{fix.explanation}</p>
        <div className="overflow-x-auto rounded-md font-mono text-xs">
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
          <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
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
            stroke="var(--chart-1)"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="costCents"
            name="Cost (¢)"
            stroke="var(--chart-2)"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function StatTile({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string;
  icon: React.ElementType;
  accent?: "destructive" | "primary";
}) {
  return (
    <div className="bg-card flex items-center gap-3 rounded-xl border p-4">
      <div
        className={
          "flex size-9 shrink-0 items-center justify-center rounded-lg " +
          (accent === "destructive"
            ? "bg-destructive/10 text-destructive"
            : "bg-primary/10 text-primary")
        }
      >
        <Icon className="size-4" />
      </div>
      <div>
        <p className="text-lg leading-none font-semibold tabular-nums">{value}</p>
        <p className="text-muted-foreground text-xs">{label}</p>
      </div>
    </div>
  );
}

function KillSwitchButton({
  active,
  busy,
  onToggle,
}: {
  active: boolean;
  busy: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      onClick={onToggle}
      disabled={busy}
      variant={active ? "destructive" : "outline"}
      size="sm"
      className="gap-1.5"
    >
      <Square className="size-3.5 fill-current" />
      {active ? "Halted" : "Kill switch"}
    </Button>
  );
}

function Nav({
  killActive,
  killBusy,
  onToggleKillSwitch,
}: {
  killActive: boolean;
  killBusy: boolean;
  onToggleKillSwitch: () => void;
}) {
  return (
    <header className="border-border/60 bg-background/80 sticky top-0 z-10 border-b backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
        <div className="flex items-center gap-2">
          <div className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md">
            <ShieldCheck className="size-4" />
          </div>
          <span className="font-heading font-semibold tracking-tight">Sentinel</span>
        </div>
        <div className="flex items-center gap-2">
          <KillSwitchButton active={killActive} busy={killBusy} onToggle={onToggleKillSwitch} />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function Hero({
  repo,
  onRepoChange,
  branch,
  onBranchChange,
  onRun,
  running,
  connecting,
  disabled,
}: {
  repo: string;
  onRepoChange: (v: string) => void;
  branch: string;
  onBranchChange: (v: string) => void;
  onRun: () => void;
  running: boolean;
  connecting: boolean;
  disabled: boolean;
}) {
  const steps = [
    {
      icon: ShieldCheck,
      title: "Security Analyst",
      desc: "Real semgrep + pip-audit findings, grounded — not freehand-detected.",
    },
    {
      icon: Sparkles,
      title: "Quality Analyst",
      desc: "Dead code and unused imports, flagged with evidence and reasoning.",
    },
    {
      icon: FlaskConical,
      title: "Test Analyst",
      desc: "Untested functions surfaced so coverage gaps aren't invisible.",
    },
  ];

  return (
    <div className="flex flex-col items-center gap-10 py-16 text-center">
      <div className="flex flex-col items-center gap-4">
        <Badge variant="secondary" className="gap-1.5 px-3 py-1">
          <Radio className="text-primary size-3" />
          Multi-agent · LangGraph
        </Badge>
        <h1 className="font-heading max-w-2xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          An AI engineer that audits your codebase
        </h1>
        <p className="text-muted-foreground max-w-lg text-balance">
          Three specialized analysts investigate your repo in parallel, then Sentinel
          opens validated, explained fix PRs. Nothing merges without you.
        </p>
      </div>

      <div className="flex w-full max-w-lg flex-col gap-2">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={repo}
            onChange={(e) => onRepoChange(e.target.value)}
            placeholder="GitHub URL or local path"
            aria-label="Repository URL or local path"
            className="h-11 font-mono text-sm"
          />
          {isGitHubUrl(repo) && (
            <Input
              value={branch}
              onChange={(e) => onBranchChange(e.target.value)}
              placeholder="Branch (default)"
              aria-label="Branch"
              className="h-11 font-mono text-sm sm:w-40"
            />
          )}
          <Button onClick={onRun} disabled={running || connecting || disabled} size="lg" className="gap-2">
            {running || connecting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Play className="size-4" />
            )}
            {connecting ? "Connecting…" : running ? "Running…" : "Run audit"}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          Paste a public GitHub repo URL, or a local path if you&apos;re running Sentinel
          against a repo already on this machine.
        </p>
      </div>

      <div className="grid w-full max-w-3xl grid-cols-1 gap-4 sm:grid-cols-3">
        {steps.map((s) => (
          <div key={s.title} className="bg-card flex flex-col items-center gap-2 rounded-xl border p-5 text-center">
            <div className="bg-primary/10 text-primary flex size-9 items-center justify-center rounded-lg">
              <s.icon className="size-4" />
            </div>
            <p className="text-sm font-medium">{s.title}</p>
            <p className="text-muted-foreground text-xs leading-relaxed">{s.desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Home() {
  const [repo, setRepo] = useState(DEFAULT_REPO);
  const [branch, setBranch] = useState("");
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [running, setRunning] = useState(false);
  const [connecting, setConnecting] = useState(false);
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

  async function handleRunAudit() {
    setError(null);

    let targetPath = repo;
    if (isGitHubUrl(repo)) {
      setConnecting(true);
      try {
        const connected = await connectRepo(repo, branch);
        targetPath = connected.local_path;
      } catch (err) {
        setError(String(err instanceof Error ? err.message : err));
        setConnecting(false);
        return;
      }
      setConnecting(false);
    }

    setRunning(true);
    setTimeline([]);
    setExpanded(new Set());
    seqRef.current = 0;

    const ws = new WebSocket(liveAuditSocketUrl(targetPath));

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

  const hasRunSomething = Boolean(audit) || running || timeline.length > 0;
  const riskyCount = audit?.findings.filter((f) => f.risk_tier === "risky").length ?? 0;
  const prCount =
    audit?.findings.filter((f) => f.pr_status === "opened" || f.pr_status === "opened_draft")
      .length ?? 0;

  return (
    <div className="flex flex-1 flex-col">
      <Nav killActive={killActive} killBusy={killBusy} onToggleKillSwitch={handleToggleKillSwitch} />

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 pb-16">
        {!hasRunSomething ? (
          <Hero
            repo={repo}
            onRepoChange={setRepo}
            branch={branch}
            onBranchChange={setBranch}
            onRun={handleRunAudit}
            running={running}
            connecting={connecting}
            disabled={killActive}
          />
        ) : (
          <div className="flex flex-col gap-2 pt-8 sm:flex-row sm:items-center">
            <Input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="GitHub URL or local path"
              aria-label="Repository URL or local path"
              className="font-mono text-sm"
            />
            {isGitHubUrl(repo) && (
              <Input
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="Branch (default)"
                aria-label="Branch"
                className="font-mono text-sm sm:w-40"
              />
            )}
            <Button
              onClick={handleRunAudit}
              disabled={running || connecting || killActive}
              className="gap-2"
            >
              {running || connecting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Play className="size-4" />
              )}
              {connecting ? "Connecting…" : running ? "Running…" : "Run audit"}
            </Button>
          </div>
        )}

        {killActive && (
          <p className="text-destructive text-sm">
            Kill switch is active — no new audits or agent actions will run until deactivated.
          </p>
        )}
        {error && <p className="text-destructive text-sm">Error: {error}</p>}

        {(running || timeline.length > 0) && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {running && <Loader2 className="text-primary size-4 animate-spin" />}
                Live audit
              </CardTitle>
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

        {audit && (
          <>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile label="Findings" value={String(audit.findings.length)} icon={Bug} />
              <StatTile
                label="Risky"
                value={String(riskyCount)}
                icon={ShieldCheck}
                accent="destructive"
              />
              <StatTile label="PRs opened" value={String(prCount)} icon={GitPullRequest} />
              <StatTile
                label="Cost"
                value={audit.cost_usd !== undefined ? `$${audit.cost_usd.toFixed(3)}` : "—"}
                icon={Sparkles}
              />
            </div>

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
                      {audit.findings.map((f, i) => {
                        const AnalystIcon = ANALYST_ICON[f.analyst ?? ""];
                        return (
                          <Fragment key={i}>
                            <TableRow
                              className={f.proposed_fix ? "cursor-pointer" : undefined}
                              onClick={() => f.proposed_fix && toggleExpanded(i)}
                            >
                              <TableCell className="capitalize">
                                <span className="flex items-center gap-1.5">
                                  {AnalystIcon && (
                                    <AnalystIcon className="text-muted-foreground size-3.5" />
                                  )}
                                  {f.analyst ?? "—"}
                                </span>
                              </TableCell>
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
                        );
                      })}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </>
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
        <p className="text-muted-foreground pb-2 text-center text-xs">
          Sentinel never merges anything automatically. Every PR — mechanical or risky — is
          opened for human review; risky fixes open as drafts and are always labeled
          needs-security-review.
        </p>
      </main>
    </div>
  );
}
