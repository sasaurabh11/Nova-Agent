import { useEffect, useMemo, useRef, useState } from "react";
import { getCustomers, getHealth, getShipment, runQuery, uploadShipment } from "./api";
import InboxView from "./InboxView";
import ShipmentResult from "./ShipmentResult";

type Tab = "inbox" | "upload";

export default function App() {
  const [tab, setTab] = useState<Tab>("inbox");
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setErr("Cannot reach the API on :8099 — is the backend running?"));
  }, []);

  const uncertain = health?.uncertain_threshold ?? 0.7;
  const auto = health?.auto_approve_threshold ?? 0.85;

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
          <nav className="tabs">
            <button className={tab === "inbox" ? "active" : ""} onClick={() => setTab("inbox")}>
              📥 CG Inbox
            </button>
            <button className={tab === "upload" ? "active" : ""} onClick={() => setTab("upload")}>
              📄 Upload
            </button>
          </nav>
          <div className="appbar-right">
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
        {tab === "inbox" ? (
          <InboxView uncertain={uncertain} auto={auto} />
        ) : (
          <UploadView uncertain={uncertain} auto={auto} />
        )}
      </main>
    </div>
  );
}

function UploadView({ uncertain, auto }: { uncertain: number; auto: number }) {
  const [customers, setCustomers] = useState<any[]>([]);
  const [customer, setCustomer] = useState("cust_acme");
  const [files, setFiles] = useState<FileList | null>(null);
  const [shipmentId, setShipmentId] = useState<string | null>(null);
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("how many shipments were flagged for review this week?");
  const [qres, setQres] = useState<any>(null);
  const [qbusy, setQbusy] = useState(false);
  const poll = useRef<number | null>(null);

  useEffect(() => {
    getCustomers().then(setCustomers).catch(() => {});
    return () => {
      if (poll.current) clearInterval(poll.current);
    };
  }, []);

  const fileName = useMemo(
    () => (files && files.length ? Array.from(files).map((f) => f.name).join(", ") : ""),
    [files],
  );

  async function onRun() {
    setErr(null);
    setData(null);
    if (!files || files.length === 0) {
      setErr("Choose a document first.");
      return;
    }
    setBusy(true);
    try {
      const { shipment_id } = await uploadShipment(customer, files);
      setShipmentId(shipment_id);
      if (poll.current) clearInterval(poll.current);
      poll.current = window.setInterval(async () => {
        const full = await getShipment(shipment_id);
        setData(full);
        const failed = (full.runs ?? []).some((r: any) => r.status === "error");
        if (full.decision || full.shipment.status !== "processing" || failed) {
          if (poll.current) clearInterval(poll.current);
          setBusy(false);
          if (failed && !full.decision) {
            const msg = full.runs.find((r: any) => r.status === "error")?.error ?? "error";
            setErr(`Pipeline failed: ${String(msg).slice(0, 200)}`);
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

  return (
    <>
      {err && <div className="banner error">⚠ {err}</div>}
      <section className="panel">
        <div className="panel-head">
          <div>
            <div className="eyebrow">Manual run</div>
            <h2>Upload document(s) through the pipeline</h2>
          </div>
        </div>
        <div className="uploader">
          <div className="field">
            <label>Customer</label>
            <select value={customer} onChange={(e) => setCustomer(e.target.value)}>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field grow">
            <label>Document(s)</label>
            <label className="filepick">
              <input
                type="file"
                multiple
                accept=".pdf,.png,.jpg,.jpeg"
                onChange={(e) => setFiles(e.target.files)}
              />
              <span className="filepick-btn">Choose files</span>
              <span className="filepick-name">{fileName || "No file selected"}</span>
            </label>
          </div>
          <button className="btn primary" onClick={onRun} disabled={busy}>
            {busy ? "Running…" : "Run pipeline"}
          </button>
        </div>
        <div className="samples">
          <span>Samples:</span>
          <code>commercial_invoice_acme.pdf</code>
          <em>approve</em>
          <code>commercial_invoice_mismatch.pdf</code>
          <em>amendment</em>
          <code>commercial_invoice_incomplete.pdf</code>
          <em>flag</em>
        </div>
      </section>

      {shipmentId && data && (
        <section className="panel">
          <ShipmentResult data={data} uncertain={uncertain} auto={auto} />
        </section>
      )}

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
          </div>
        )}
      </section>
    </>
  );
}
