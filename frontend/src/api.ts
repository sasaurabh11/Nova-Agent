// Thin API client. Base URL overridable via VITE_API_BASE.
const BASE = (import.meta as any).env?.VITE_API_BASE ?? "http://localhost:8099";

async function j(res: Response) {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const getHealth = () => fetch(`${BASE}/health`).then(j);
export const getCustomers = () => fetch(`${BASE}/customers`).then(j);

export async function uploadShipment(customerId: string, files: FileList) {
  const fd = new FormData();
  fd.append("customer_id", customerId);
  Array.from(files).forEach((f) => fd.append("files", f));
  return fetch(`${BASE}/shipments`, { method: "POST", body: fd }).then(j);
}
export const getShipment = (id: string) => fetch(`${BASE}/shipments/${id}`).then(j);
export const runQuery = (question: string) =>
  fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  }).then(j);

export const getInbox = () => fetch(`${BASE}/inbox`).then(j);
export const getEmail = (id: string) => fetch(`${BASE}/inbox/${id}`).then(j);

export async function dropEmail(sender: string, subject: string, files: FileList) {
  const fd = new FormData();
  fd.append("sender", sender);
  fd.append("subject", subject);
  Array.from(files).forEach((f) => fd.append("files", f));
  return fetch(`${BASE}/inbox/emails`, { method: "POST", body: fd }).then(j);
}

export const editReply = (id: string, subject: string, body: string) =>
  fetch(`${BASE}/replies/${id}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ subject, body }),
  }).then(j);

export const sendReply = (id: string) =>
  fetch(`${BASE}/replies/${id}/send`, { method: "POST" }).then(j);
