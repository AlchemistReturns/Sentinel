"use client";

import { useEffect, useState } from "react";

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
import { type AuditResult, fetchLatestAudit, runAudit } from "@/lib/api";

const DEFAULT_REPO = "E:/Abrar/AI_ML/Sentinel";

export default function Home() {
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLatestAudit()
      .then(setAudit)
      .catch((err) => setError(String(err)));
  }, []);

  async function handleRunAudit() {
    setLoading(true);
    setError(null);
    try {
      const result = await runAudit(DEFAULT_REPO);
      setAudit(result);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sentinel</h1>
          <p className="text-muted-foreground text-sm">
            Autonomous codebase auditor — Quality Analyst findings
          </p>
        </div>
        <Button onClick={handleRunAudit} disabled={loading}>
          {loading ? "Running audit…" : "Run audit"}
        </Button>
      </div>

      {error && (
        <p className="text-destructive text-sm">Error: {error}</p>
      )}

      {!audit && !loading && !error && (
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
              <p className="text-muted-foreground text-sm">
                No unused imports found.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>File</TableHead>
                    <TableHead>Line</TableHead>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Explanation</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.findings.map((f, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">
                        {f.file_path}
                      </TableCell>
                      <TableCell>{f.line}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{f.symbol}</Badge>
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
