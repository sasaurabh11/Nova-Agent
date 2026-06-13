import { useEffect, useMemo, useRef, useState } from "react";
import {
  getCustomers,
  getHealth,
  getShipment,
  runQuery,
  uploadShipment,
} from "./api";

type Field = {
  value: string | null;
  confidence: number;
  status: "found" | "not_found";
  source_snippet: string | null;
  page: number | null;
};
type ValRow = {
  field: string;
  found: string | null;
  expected: string;
  status: "match" | "mismatch" | "uncertain";
  confidence: number;
  reason: string;
};

const DECISION = {
  auto_approve: { label: "Auto-approved", icon: "✓", cls: "approve" },
  flag_for_review: { label: "Flagged for review", icon: "!", cls: "flag" },
  request_amendment: { label: "Amendment requested", icon: "✎", cls: "amend" },
} as const;

const STAGE_LABEL: Record<string, string> = {
  extract: "Extract",
  validate: "Validate",
  decide: "Decide",
};

const prettyField = (s: string) =>
  s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export default function App() {
  const [health, setHealth] = useState<any>(null);
  const [customers, setCustomers] = useState<any[]>([]);
  const [customer, setCustomer] = useState("cust_acme");
  const [files, setFiles] = useState<FileList | null>(null);
  const [shipmentId, setShipmentId] = useState<string | null>(null);
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState(
    "how many shipments were flagged for review this week?",
  );
  const [qres, setQres] = useState<any>(null);
  const [qbusy, setQbusy] = useState(false);
  const poll = useRef<number | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setErr("Cannot reach the API — is the backend running on :8099?"));
    getCustomers()
      .then(setCustomers)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const body = data?.decision?.draft_amendment?.body;
    if (body !== undefined && body !== null) setDraft(body);
  }, [data?.decision?.draft_amendment?.body]);

  function stopPolling() {
    if (poll.current) {
      clearInterval(poll.current);
      poll.current = null;
    }
  }

  async function onRun() {
    setErr(null);
    setData(null);
    setDraft("");
    setQres(null);
    if (!files || files.length === 0) {
      setErr("Choose a document first.");
      return;
    }
    setBusy(true);
    try {
      const { shipment_id } = await uploadShipment(customer, files);
      setShipmentId(shipment_id);
      stopPolling();
      poll.current = window.setInterval(async () => {
        const full = await getShipment(shipment_id);
        setData(full);
        const failed = (full.runs ?? []).some((r: any) => r.status === "error");
        if (full.decision || full.shipment.status !== "processing" || failed) {
          stopPolling();
          setBusy(false);
          if (failed && !full.decision) {
            const msg =
              full.runs.find((r: any) => r.status === "error")?.error ??
              "pipeline error";
            setErr(`Pipeline failed at extraction: ${String(msg).slice(0, 200)}`);
          }
        }
      }, 2000);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  async function onQuery() {
    if (!q.trim()) return;
    setQbusy(true);
    try {
      setQres(await runQuery(q));
    } finally {
      setQbusy(false);
    }
  }

  const uncertain = health?.uncertain_threshold ?? 0.7;
  const auto = health?.auto_approve_threshold ?? 0.85;
  const ext = data?.extractions?.[0]?.payload;
  const val = data?.validations?.[0]?.payload;
  const decision = data?.decision;
  const stages = data?.stages;
  const status = data?.shipment?.status;
  const fileName = useMemo(
    () => (files && files.length ? files[0].name : ""),
    [files],
  );

  function badge(conf: number, status: string) {
    if (status === "not_found") return "red";
    if (conf >= auto) return "green";
    if (conf >= uncertain) return "amber";
    return "red";
  }

  const dmeta = decision ? (DECISION as any)[decision.decision] : null;

  return (
    <div className="app">
      <div className="appbar">
        <div className="appbar-inner">
          <div className="brand">
            <div className="logo">N</div>
            <div>
              <div className="brand-title">Nova</div>
              <div className="brand-sub">Trade Document Pipeline</div>
            </div>
          </div>
          <div className="appbar-right">
            <span className="pipe">
              Extractor <i>→</i> Validator <i>→</i> Router
            </span>
            {health ? (
              <span className="model-badge">
                <span className="live" /> {health.model}
              </span>
            ) : (
              <span className="model-badge muted">connecting…</span>
            )}
          </div>
        </div>
      </div>

      <main className="container">
        {err && <div className="banner error">⚠ {err}</div>}

        {/* Upload */}
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">New verification</div>
              <h2>Run a document through the pipeline</h2>
            </div>
          </div>
          <div className="uploader">
            <div className="field">
              <label>Customer</label>
              <select
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
              >
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field grow">
              <label>Document</label>
              <label className="filepick">
                <input
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg"
                  onChange={(e) => setFiles(e.target.files)}
                />
                <span className="filepick-btn">Choose file</span>
                <span className="filepick-name">
                  {fileName || "No file selected"}
                </span>
              </label>
            </div>
            <button className="btn primary" onClick={onRun} disabled={busy}>
              {busy ? "Running…" : "Run pipeline"}
            </button>
          </div>
          <div className="samples">
            <span>Samples:</span>
            <code>commercial_invoice_acme.pdf</code>
            <em>auto-approve</em>
            <code>commercial_invoice_mismatch.pdf</code>
            <em>amendment</em>
            <code>commercial_invoice_incomplete.pdf</code>
            <em>flag for review</em>
          </div>
        </section>

        {/* Stepper */}
        {shipmentId && (
          <section className="panel">
            <div className="stepper">
              {["extract", "validate", "decide"].map((s, i) => {
                const st = stages?.[s] ?? "pending";
                return (
                  <div key={s} className={`step ${st}`}>
                    <div className="step-dot">
                      {st === "done" ? "✓" : st === "error" ? "✕" : i + 1}
                    </div>
                    <div className="step-label">{STAGE_LABEL[s]}</div>
                    {i < 2 && <div className="step-bar" />}
                  </div>
                );
              })}
            </div>
            <div className="ship-row">
              <span className="mono">{shipmentId}</span>
              {status && (
                <span className={`status-chip ${status}`}>
                  {status.replace(/_/g, " ")}
                </span>
              )}
            </div>
          </section>
        )}

        {/* Extracted fields */}
        {ext && (
          <section className="panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">Extractor</div>
                <h2>Extracted fields</h2>
              </div>
              <span className="tag doc">{ext.doc_type}</span>
            </div>
            <div className="table-wrap">
              <table className="kv">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Value</th>
                    <th>Confidence</th>
                    <th>Source snippet</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(ext.fields as Record<string, Field>).map(
                    ([name, f]) => (
                      <tr key={name}>
                        <td className="fname">{prettyField(name)}</td>
                        <td>
                          {f.value ?? <em className="muted">not found</em>}
                        </td>
                        <td>
                          <span
                            className={`pill ${badge(f.confidence, f.status)}`}
                          >
                            {(f.confidence * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td className="snippet" title={f.source_snippet ?? ""}>
                          {f.source_snippet ?? "—"}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
            {ext.warnings?.length > 0 && (
              <div className="note warn">⚠ {ext.warnings.join(" · ")}</div>
            )}
          </section>
        )}

        {/* Validation */}
        {val && (
          <section className="panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">Validator</div>
                <h2>Field-by-field validation</h2>
              </div>
              <div className="counts">
                <span className="count match">{val.summary.match} match</span>
                <span className="count mismatch">
                  {val.summary.mismatch} mismatch
                </span>
                <span className="count uncertain">
                  {val.summary.uncertain} uncertain
                </span>
              </div>
            </div>
            <div className="table-wrap">
              <table className="kv">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Found</th>
                    <th>Expected</th>
                    <th>Status</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(val.results as ValRow[]).map((r) => (
                    <tr key={r.field}>
                      <td className="fname">{prettyField(r.field)}</td>
                      <td>{r.found ?? <em className="muted">—</em>}</td>
                      <td className="muted">{r.expected}</td>
                      <td>
                        <span className={`tag ${r.status}`}>{r.status}</span>
                      </td>
                      <td className="reason">{r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Decision */}
        {decision && dmeta && (
          <section className={`panel decision ${dmeta.cls}`}>
            <div className="decision-head">
              <div className={`decision-icon ${dmeta.cls}`}>{dmeta.icon}</div>
              <div>
                <div className="eyebrow">Router decision</div>
                <h2>{dmeta.label}</h2>
              </div>
              {decision.requires_human && (
                <span className="human-chip">needs human</span>
              )}
            </div>
            <blockquote className="reasoning">{decision.reasoning}</blockquote>

            {decision.discrepancies?.length > 0 && (
              <div className="disc-list">
                {decision.discrepancies.map((d: any, i: number) => (
                  <div key={i} className="disc">
                    <span className={`tag ${d.status}`}>{d.status}</span>
                    <b>{prettyField(d.field)}</b>
                    <span className="muted">
                      found {d.found ?? "—"} · expected {d.expected}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {decision.draft_amendment && (
              <div className="composer">
                <div className="composer-head">
                  <span>✉ Draft reply to supplier</span>
                  <span className="composer-note">
                    Review required · agent never sends on its own
                  </span>
                </div>
                <div className="composer-subject">
                  {decision.draft_amendment.subject}
                </div>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={10}
                />
                <div className="composer-actions">
                  <button
                    className="btn ghost"
                    disabled
                    title="Sending is disabled — the agent never sends on its own."
                  >
                    Send (disabled)
                  </button>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Totals */}
        {data?.totals && (
          <div className="stats">
            <div className="stat">
              <div className="stat-val">{data.totals.tokens}</div>
              <div className="stat-label">tokens</div>
            </div>
            <div className="stat">
              <div className="stat-val">${data.totals.cost_usd.toFixed(5)}</div>
              <div className="stat-label">est. cost</div>
            </div>
            <div className="stat">
              <div className="stat-val">
                {(data.totals.latency_ms / 1000).toFixed(1)}s
              </div>
              <div className="stat-label">latency</div>
            </div>
          </div>
        )}

        {/* Query */}
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">Query</div>
              <h2>Ask the stored data</h2>
            </div>
          </div>
          <div className="query-row">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onQuery()}
              placeholder="e.g. how many shipments were flagged this week?"
            />
            <button className="btn primary" onClick={onQuery} disabled={qbusy}>
              {qbusy ? "Asking…" : "Ask"}
            </button>
          </div>
          {qres && (
            <div className="qres">
              <div className={`answer ${qres.error ? "bad" : ""}`}>
                {qres.error ? qres.error : qres.answer}
              </div>
              {qres.sql && <pre className="sql">{qres.sql}</pre>}
              {qres.rows?.length > 0 && (
                <div className="table-wrap">
                  <table className="kv">
                    <thead>
                      <tr>
                        {Object.keys(qres.rows[0]).map((k) => (
                          <th key={k}>{prettyField(k)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {qres.rows.map((r: any, i: number) => (
                        <tr key={i}>
                          {Object.values(r).map((v: any, j) => (
                            <td key={j}>{String(v)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </section>

        <footer className="foot">
          Nova · governed agent automation — extract, validate, decide, with a
          reason for every outcome.
        </footer>
      </main>
    </div>
  );
}
