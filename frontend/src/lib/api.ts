export type Finding = {
  file_path: string;
  line: number;
  symbol: string;
  evidence: string;
  explanation: string;
};

export type AuditResult = {
  audit_id: string;
  repo: string;
  findings: Finding[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
