import { useEffect, useRef, useState } from "react";
import { dropEmail, getEmail, getInbox } from "./api";
import ShipmentResult from "./ShipmentResult";

const STATUS_LABEL: Record<string, string> = {
  received: "Incoming",
  processing: "Processing",
  verified: "Verified",
  replied: "Replied",
};

function timeago(iso: string) {
  return (iso ?? "").slice(0, 16).replace("T", " ");
}

export default function InboxView({
  uncertain,
  auto,
}: {
  uncertain: number;
  auto: number;
}) {
  const [emails, setEmails] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [showSim, setShowSim] = useState(false);
  const [sender, setSender] = useState("shanghai-electronics@su.example");
  const [subject, setSubject] = useState("Shipment ACME-1001 — documents");
  const [files, setFiles] = useState<FileList | null>(null);
  const tick = useRef<number | null>(null);

  async function refresh() {
    try {
      setEmails(await getInbox());
      if (selected) setDetail(await getEmail(selected));
    } catch {
      /* transient */
    }
  }

  useEffect(() => {
    refresh();
    tick.current = window.setInterval(refresh, 3000);
    return () => {
      if (tick.current) clearInterval(tick.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  async function onSimulate() {
    if (!files || files.length === 0) {
      alert("Choose one or more document files first.");
      return;
    }
    const { email_id } = await dropEmail(sender, subject, files);
    setShowSim(false);
    setSelected(email_id);
    refresh();
  }

  const ship = detail?.shipment;
  const email = detail?.email;

  return (
    <div className="inbox">
      {/* Left: the CG inbox list */}
      <aside className="inbox-list">
        <div className="inbox-list-head">
          <span>Inbox</span>
          <button className="btn tiny" onClick={() => setShowSim((s) => !s)}>
            + Simulate
          </button>
        </div>

        {showSim && (
          <div className="sim">
            <label>From (SU)</label>
            <input value={sender} onChange={(e) => setSender(e.target.value)} />
            <label>Subject</label>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} />
            <label>Attachments</label>
            <input
              type="file"
              multiple
              accept=".pdf,.png,.jpg,.jpeg"
              onChange={(e) => setFiles(e.target.files)}
            />
            <button className="btn primary" onClick={onSimulate}>
              Send to inbox
            </button>
            <div className="hint">
              Feeds the same queue the live IMAP poller uses.
            </div>
          </div>
        )}

        {emails.length === 0 && (
          <div className="empty">
            No emails yet. Email your Nova mailbox (with PDF attachments), or use
            <b> + Simulate</b>.
          </div>
        )}
        {emails.map((e) => (
          <button
            key={e.id}
            className={`mail ${selected === e.id ? "active" : ""}`}
            onClick={() => {
              setSelected(e.id);
              setDetail(null);
            }}
          >
            <div className="mail-top">
              <span className="mail-from">{e.sender}</span>
              <span className={`dot-status ${e.status}`} />
            </div>
            <div className="mail-subject">{e.subject}</div>
            <div className="mail-meta">
              <span className={`status-chip ${e.status}`}>
                {STATUS_LABEL[e.status] ?? e.status}
              </span>
              <span className="muted">{timeago(e.received_at)}</span>
            </div>
          </button>
        ))}
      </aside>

      {/* Right: detail / pipeline status for the selected email */}
      <section className="inbox-detail">
        {!selected && (
          <div className="placeholder">
            Select an email to see what the agent found and the draft reply.
          </div>
        )}
        {selected && email && (
          <>
            <div className="detail-head">
              <div>
                <div className="eyebrow">{STATUS_LABEL[email.status] ?? email.status}</div>
                <h2>{email.subject}</h2>
                <div className="muted">
                  from <b>{email.sender}</b> · {timeago(email.received_at)}
                </div>
              </div>
            </div>

            {!ship && (
              <div className="placeholder pulse">
                📥 Email received — the agent is processing the attachments…
              </div>
            )}
            {ship && (
              <ShipmentResult
                data={ship}
                uncertain={uncertain}
                auto={auto}
                onChanged={refresh}
              />
            )}
          </>
        )}
      </section>
    </div>
  );
}
