export type ProposedFix = {
  old_snippet: string;
  new_snippet: string;
  explanation: string;
  unfixable: boolean;
};

export type Validation = {
  lint_passed: boolean;
  tests_passed: boolean;
  lint_output: string;
  test_output: string;
  passed: boolean;
};

export type PrStatus =
  | "not_auto_fixable"
  | "validation_failed"
  | "duplicate_skipped"
  | "opened"
  | "opened_draft"
  | "halted";

export type Finding = {
  file_path: string;
  line: number;
  symbol: string;
  evidence: string;
  explanation: string;
  analyst?: "security" | "quality" | "test";
  risk_tier?: "mechanical" | "risky";
  semantic_cache_hit?: boolean;
  proposed_fix?: ProposedFix | null;
  validation?: Validation;
  pr_url?: string;
  pr_status?: PrStatus;
};

export type AuditResult = {
  audit_id: string;
  repo: string;
  findings: Finding[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS_URL = API_URL.replace(/^http/, "ws");

export async function fetchLatestAudit(): Promise<AuditResult | null> {
  const res = await fetch(`${API_URL}/api/audits/latest`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to fetch latest audit: ${res.status}`);
  return res.json();
}

export async function runAudit(repo: string): Promise<AuditResult> {
  const res = await fetch(`${API_URL}/api/audits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo }),
  });
  if (!res.ok) throw new Error(`Failed to run audit: ${res.status}`);
  return res.json();
}

export function liveAuditSocketUrl(repo: string): string {
  return `${WS_URL}/ws/audits?repo=${encodeURIComponent(repo)}`;
}

export async function fetchKillSwitch(): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/kill-switch`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch kill switch: ${res.status}`);
  const data = await res.json();
  return data.active;
}

export async function setKillSwitch(active: boolean): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/kill-switch/${active ? "activate" : "deactivate"}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to set kill switch: ${res.status}`);
  const data = await res.json();
  return data.active;
}

// -- Live event parsing --------------------------------------------------
//
// The orchestrator streams raw LangGraph "updates" events over the websocket:
// top-level graph nodes (ingest, scope, diagnose, ...) arrive with an empty
// namespace; sub-agent internals (each analyst's own model/tool-call loop)
// arrive namespaced as "<node_name>:<run_id>". We turn those into a flat,
// human-readable timeline.

export type TimelineEntry = {
  id: string;
  label: string;
  detail?: string;
};

type RawEvent =
  | { event: "update"; namespace: string[]; update: Record<string, unknown> }
  | ({ event: "done" } & AuditResult);

function analystLabel(namespace: string[]): string | null {
  if (namespace.length === 0) return null;
  const [node] = namespace[0].split(":");
  return node
    .split("_")
    .map((w) => w[0]?.toUpperCase() + w.slice(1))
    .join(" ");
}

export function parseTimelineEntry(raw: string, seq: number): TimelineEntry | null {
  const data = JSON.parse(raw) as RawEvent;

  if (data.event === "done") {
    return { id: `done-${seq}`, label: `Audit complete — ${data.findings.length} finding(s)` };
  }

  const { namespace, update } = data;
  const who = analystLabel(namespace);

  // Top-level graph node finished.
  if (namespace.length === 0) {
    const [nodeName] = Object.keys(update);
    if (!nodeName) return null;
    const label = nodeName
      .split("_")
      .map((w) => w[0]?.toUpperCase() + w.slice(1))
      .join(" ");
    return { id: `${seq}`, label: `✓ ${label} finished` };
  }

  // Sub-agent model step: about to call a tool (or produced a final answer).
  if ("model" in update) {
    const msg = (update as any).model?.messages?.[0];
    const toolCalls = msg?.tool_calls as { name: string }[] | undefined;
    if (toolCalls && toolCalls.length > 0) {
      const names = toolCalls.map((t) => t.name).join(", ");
      return { id: `${seq}`, label: `${who} → calling ${names}` };
    }
    return { id: `${seq}`, label: `${who} → reasoning` };
  }

  // Sub-agent tool step: a tool call returned.
  if ("tools" in update) {
    const msgs = ((update as any).tools?.messages ?? []) as {
      name: string;
      content: string;
    }[];
    const detail = msgs
      .map((m) => `${m.name}: ${String(m.content).slice(0, 120)}`)
      .join(" | ");
    return { id: `${seq}`, label: `${who} ← tool result`, detail };
  }

  return { id: `${seq}`, label: `${who ?? "graph"} update` };
}
