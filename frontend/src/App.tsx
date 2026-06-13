import { useEffect, useRef, useState } from "react";
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

const DECISION_LABEL: Record<string, string> = {
  auto_approve: "Auto-approved",
  flag_for_review: "Flagged for review",
  request_amendment: "Amendment requested",
};

export default function App() {
  const [health, setHealth] = useState<any>(null);
  const [customers, setCustomers] = useState<any[]>([]);
  const [customer, setCustomer] = useState("cust_acme");
  const [files, setFiles] = useState<FileList | null>(null);
  const [shipmentId, setShipmentId] = useState<string | null>(null);
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState(
    "how many shipments were flagged for review this week?",
  );
  const [qres, setQres] = useState<any>(null);
  const poll = useRef<number | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() =>
        setErr("Cannot reach API — is the backend running?"),
      );
    getCustomers()
      .then(setCustomers)
      .catch(() => {});
  }, []);

  useEffect(() => {
    // sync draft textarea when a new decision arrives
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
    if (!files || files.length === 0) {
      setErr(
        "Choose a document first.",
      );
      return;
    }
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
          if (failed && !full.decision) {
            const msg =
              full.runs.find((r: any) => r.status === "error")?.error ??
              "pipeline error";
            setErr(
              `Pipeline failed at extraction: ${String(msg).slice(0, 200)}`,
            );
          }
        }
      }, 2000);
    } catch (e: any) {
      setErr(e.message);
    }
  }

  async function onQuery() {
    setQres(await runQuery(q));
  }

  const uncertain = health?.uncertain_threshold ?? 0.7;
  const auto = health?.auto_approve_threshold ?? 0.85;
  const ext = data?.extractions?.[0]?.payload;
  const val = data?.validations?.[0]?.payload;
  const decision = data?.decision;
  const stages = data?.stages;

  function badge(conf: number, status: string) {
    if (status === "not_found") return "red";
    if (conf >= auto) return "green";
    if (conf >= uncertain) return "amber";
    return "red";
  }

  return (
    <div className="wrap">
      <header>
        <h1>Nova · Agent</h1>
      </header>

      {err && <div className="error">{err}</div>}

      <section className="card">
        <div className="row">
          <label>
            Customer&nbsp;
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
          </label>
          <input
            type="file"
            accept=".pdf,.png,.jpg,.jpeg"
            onChange={(e) => setFiles(e.target.files)}
          />
          <button onClick={onRun}>Run pipeline</button>
        </div>
        <div className="hint">
          Try <code>samples/clean/commercial_invoice_acme.pdf</code>{" "}
          (auto-approve),{" "}
          <code>samples/clean/commercial_invoice_mismatch.pdf</code>{" "}
          (amendment), <code>samples/messy/commercial_invoice_scan.png</code>{" "}
          (flag for review).
        </div>
      </section>

      {shipmentId && (
        <section className="card">
          <div className="timeline">
            {["extract", "validate", "decide"].map((s) => (
              <div key={s} className={`stage ${stages?.[s] ?? "pending"}`}>
                <div className="dot" />
                <span>{s}</span>
              </div>
            ))}
          </div>
          <div className="ship">
            Shipment <code>{shipmentId}</code> · status{" "}
            <b>{data?.shipment?.status ?? "…"}</b>
          </div>
        </section>
      )}

      {ext && (
        <section className="card">
          <h2>
            Extracted fields <small>{ext.doc_type}</small>
          </h2>
          <table className="fields">
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
                    <td>{name}</td>
                    <td>{f.value ?? <em className="muted">not found</em>}</td>
                    <td>
                      <span className={`pill ${badge(f.confidence, f.status)}`}>
                        {f.confidence.toFixed(2)}
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
          {ext.warnings?.length > 0 && (
            <div className="warn">⚠ {ext.warnings.join(" · ")}</div>
          )}
        </section>
      )}

      {val && (
        <section className="card">
          <h2>
            Validation{" "}
            <small>
              {val.summary.match} match · {val.summary.mismatch} mismatch ·{" "}
              {val.summary.uncertain} uncertain
            </small>
          </h2>
          <table className="fields">
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
                  <td>{r.field}</td>
                  <td>{r.found ?? <em className="muted">—</em>}</td>
                  <td>{r.expected}</td>
                  <td>
                    <span className={`tag ${r.status}`}>{r.status}</span>
                  </td>
                  <td className="reason">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {decision && (
        <section className={`card decision ${decision.decision}`}>
          <h2>
            Decision · {DECISION_LABEL[decision.decision] ?? decision.decision}
            {decision.requires_human && (
              <span className="human">needs human</span>
            )}
          </h2>
          <p className="reasoning">{decision.reasoning}</p>
          {decision.draft_amendment && (
            <div className="draft">
              <div className="draft-subject">
                <b>Subject:</b> {decision.draft_amendment.subject}
              </div>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={10}
              />
              <button
                disabled
                title="Sending is disabled — agent never sends on its own."
              >
                Send
              </button>
            </div>
          )}
        </section>
      )}

      <section className="card">
        <h2>Ask the stored data</h2>
        <div className="row">
          <input
            className="grow"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onQuery()}
          />
          <button onClick={onQuery}>Ask</button>
        </div>
        {qres && (
          <div className="qres">
            <div className="answer">
              <b>Answer:</b>{" "}
              {qres.error ? (
                <span className="muted">{qres.error}</span>
              ) : (
                qres.answer
              )}
            </div>
            {qres.sql && <pre className="sql">{qres.sql}</pre>}
            {qres.rows?.length > 0 && (
              <table className="fields">
                <thead>
                  <tr>
                    {Object.keys(qres.rows[0]).map((k) => (
                      <th key={k}>{k}</th>
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
            )}
          </div>
        )}
      </section>

      {data?.totals && (
        <footer>
          <span>
            tokens <b>{data.totals.tokens}</b>
          </span>
          <span>
            est. cost <b>${data.totals.cost_usd.toFixed(5)}</b>
          </span>
          <span>
            latency <b>{data.totals.latency_ms} ms</b>
          </span>
        </footer>
      )}
    </div>
  );
}
