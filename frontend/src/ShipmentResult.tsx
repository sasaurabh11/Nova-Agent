import { useEffect, useState } from "react";
import { editReply, sendReply } from "./api";

const DECISION = {
  auto_approve: { label: "Auto-approved", icon: "✓", cls: "approve" },
  flag_for_review: { label: "Flagged for review", icon: "!", cls: "flag" },
  request_amendment: { label: "Amendment requested", icon: "✎", cls: "amend" },
} as const;

// the four pipeline nodes, in execution order
const NODES = [
  { stage: "extract", n: 1, title: "Extractor", sub: "vision → structured fields + confidence" },
  { stage: "validate", n: 2, title: "Validator", sub: "fields vs customer rules (per document)" },
  { stage: "cross_validate", n: 3, title: "Cross-Validator", sub: "consistency across all documents" },
  { stage: "decide", n: 4, title: "Router", sub: "decision + reasoning + draft reply" },
];

const prettyField = (s: string) =>
  s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export default function ShipmentResult({
  data,
  uncertain = 0.7,
  auto = 0.85,
  onChanged,
}: {
  data: any;
  uncertain?: number;
  auto?: number;
  onChanged?: () => void;
}) {
  const stages = data?.stages ?? {};
  const status = data?.shipment?.status;
  const cv = data?.cross_validation;
  const decision = data?.decision;
  const dmeta = decision ? (DECISION as any)[decision.decision] : null;
  const reply = data?.reply;

  const [subject, setSubject] = useState(reply?.subject ?? "");
  const [body, setBody] = useState(reply?.body ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setSubject(reply?.subject ?? "");
    setBody(reply?.body ?? "");
  }, [reply?.id, reply?.status]);

  function badge(conf: number, st: string) {
    if (st === "not_found") return "red";
    if (conf >= auto) return "green";
    if (conf >= uncertain) return "amber";
    return "red";
  }

  // group extraction + validation per document
  const docs = (data?.extractions ?? []).map((e: any) => {
    const doc = (data?.documents ?? []).find((d: any) => d.id === e.document_id);
    const val = (data?.validations ?? []).find((v: any) => v.document_id === e.document_id);
    return {
      filename: doc?.filename ?? e.document_id,
      doc_type: e.payload?.doc_type ?? doc?.doc_type ?? "unknown",
      fields: e.payload?.fields ?? {},
      warnings: e.payload?.warnings ?? [],
      results: val?.payload?.results ?? [],
      summary: val?.payload?.summary,
    };
  });
  const nDocs = (data?.documents ?? []).length || docs.length;

  async function onSend() {
    if (!reply) return;
    setBusy(true);
    try {
      await editReply(reply.id, subject, body);
      await sendReply(reply.id);
      onChanged?.();
    } catch (e: any) {
      alert(`Send failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const waiting = (st: string) =>
    st === "processing" ? "Running…" : st === "pending" ? "Waiting…" : "—";

  // ---- per-node content renderers ----
  function extractorBody(st: string) {
    if (!docs.length) return <div className="node-wait">{waiting(st)}</div>;
    return docs.map((d: any, i: number) => (
      <div key={i} className="docblock">
        <div className="docblock-head">
          <span className="doc-name">{d.filename}</span>
          <span className="tag doc">{d.doc_type}</span>
        </div>
        <div className="table-wrap">
          <table className="kv">
            <thead>
              <tr><th>Field</th><th>Value</th><th>Conf</th><th>Source snippet</th></tr>
            </thead>
            <tbody>
              {Object.entries(d.fields).map(([name, f]: any) => (
                <tr key={name}>
                  <td className="fname">{prettyField(name)}</td>
                  <td>{f.value ?? <em className="muted">not found</em>}</td>
                  <td>
                    <span className={`pill ${badge(f.confidence ?? 0, f.status)}`}>
                      {Math.round((f.confidence ?? 0) * 100)}%
                    </span>
                  </td>
                  <td className="snippet">{f.source_snippet ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {d.warnings.length > 0 && <div className="note warn">⚠ {d.warnings.join(" · ")}</div>}
      </div>
    ));
  }

  function validatorBody(st: string) {
    if (!docs.some((d: any) => d.results.length)) return <div className="node-wait">{waiting(st)}</div>;
    return docs.map((d: any, i: number) => (
      <div key={i} className="docblock">
        <div className="docblock-head">
          <span className="doc-name">{d.filename}</span>
          {d.summary && (
            <span className="counts-inline">
              <span className="count match">{d.summary.match}✓</span>
              <span className="count mismatch">{d.summary.mismatch}✕</span>
              <span className="count uncertain">{d.summary.uncertain}?</span>
            </span>
          )}
        </div>
        <div className="table-wrap">
          <table className="kv">
            <thead>
              <tr><th>Field</th><th>Found</th><th>Expected</th><th>Status</th><th>Reason</th></tr>
            </thead>
            <tbody>
              {d.results.map((r: any) => (
                <tr key={r.field}>
                  <td className="fname">{prettyField(r.field)}</td>
                  <td>{r.found ?? <em className="muted">—</em>}</td>
                  <td className="muted">{r.expected}</td>
                  <td><span className={`tag ${r.status}`}>{r.status}</span></td>
                  <td className="reason">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ));
  }

  function crossBody(st: string) {
    if (nDocs < 2)
      return <div className="node-wait muted">Single document — cross-check not applicable.</div>;
    if (!cv) return <div className="node-wait">{waiting(st)}</div>;
    if (cv.consistent)
      return <div className="xdoc ok">All shared fields agree across the {nDocs} documents.</div>;
    return (
      <div className="xdoc bad">
        <b>Documents disagree on {cv.conflicts.length} field(s):</b>
        {cv.conflicts.map((c: any, i: number) => (
          <div key={i} className="xconflict">
            <b>{prettyField(c.field)}</b>:{" "}
            {c.values.map((v: any, j: number) => (
              <span key={j} className="xval">{v.doc_type} = <code>{v.value}</code></span>
            ))}
          </div>
        ))}
      </div>
    );
  }

  function routerBody(st: string) {
    if (!decision || !dmeta) return <div className="node-wait">{waiting(st)}</div>;
    return (
      <>
        <div className={`decision ${dmeta.cls}`}>
          <div className="decision-head">
            <div className={`decision-icon ${dmeta.cls}`}>{dmeta.icon}</div>
            <div>
              <div className="eyebrow">Decision</div>
              <h3>{dmeta.label}</h3>
            </div>
            {decision.requires_human && <span className="human-chip">needs human</span>}
          </div>
          <blockquote className="reasoning">{decision.reasoning}</blockquote>
          {decision.discrepancies?.length > 0 && (
            <div className="disc-list">
              {decision.discrepancies.map((d: any, i: number) => (
                <div key={i} className="disc">
                  <span className={`tag ${d.status}`}>{d.status}</span>
                  <b>{prettyField(d.field)}</b>
                  <span className="muted">found {d.found ?? "—"} · expected {d.expected}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {reply && (
          <div className="composer">
            <div className="composer-head">
              <span>✉ Draft reply to supplier · {reply.kind}</span>
              <span className={reply.status === "sent" ? "sent-note" : "composer-note"}>
                {reply.status === "sent"
                  ? `sent ${(reply.sent_at ?? "").slice(0, 16).replace("T", " ")}`
                  : "review required · agent never sends on its own"}
              </span>
            </div>
            <input
              className="composer-subject-input"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={reply.status === "sent"}
            />
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={9}
              disabled={reply.status === "sent"}
            />
            <div className="composer-actions">
              {reply.status === "sent" ? (
                <button className="btn ghost" disabled>✓ Sent</button>
              ) : (
                <button className="btn primary" onClick={onSend} disabled={busy}>
                  {busy ? "Sending…" : "Review & send"}
                </button>
              )}
            </div>
          </div>
        )}
      </>
    );
  }

  const bodyFor: Record<string, (st: string) => any> = {
    extract: extractorBody,
    validate: validatorBody,
    cross_validate: crossBody,
    decide: routerBody,
  };

  return (
    <div className="result">
      {/* Live pipeline progress */}
      <div className="stepper">
        {NODES.map((nd, i) => {
          const st = stages[nd.stage] ?? "pending";
          return (
            <div key={nd.stage} className={`step ${st}`}>
              <div className="step-dot">
                {st === "done" ? "✓" : st === "error" ? "✕" : nd.n}
              </div>
              <div className="step-label">{nd.title}</div>
              {i < NODES.length - 1 && <div className="step-bar" />}
            </div>
          );
        })}
      </div>
      <div className="ship-row">
        <span className="mono">{data?.shipment?.id}</span>
        {status && <span className={`status-chip ${status}`}>{status.replace(/_/g, " ")}</span>}
        <span className="muted">{nDocs} document{nDocs === 1 ? "" : "s"}</span>
      </div>

      {/* One card per node, filling in as the run progresses */}
      {NODES.map((nd) => {
        const st = stages[nd.stage] ?? "pending";
        return (
          <div key={nd.stage} className={`node ${st}`}>
            <div className="node-head">
              <span className="node-dot">{st === "done" ? "✓" : st === "error" ? "✕" : nd.n}</span>
              <div className="node-title">
                <b>{nd.title}</b>
                <span className="node-sub">{nd.sub}</span>
              </div>
              <span className={`node-status ${st}`}>{st}</span>
            </div>
            <div className="node-body">{bodyFor[nd.stage](st)}</div>
          </div>
        );
      })}

      {data?.totals && (
        <div className="stats small">
          <div className="stat"><div className="stat-val">{data.totals.tokens}</div><div className="stat-label">tokens</div></div>
          <div className="stat"><div className="stat-val">${data.totals.cost_usd?.toFixed(5)}</div><div className="stat-label">est. cost</div></div>
          <div className="stat"><div className="stat-val">{(data.totals.latency_ms / 1000).toFixed(1)}s</div><div className="stat-label">latency</div></div>
        </div>
      )}
    </div>
  );
}
