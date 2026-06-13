const BASE = (import.meta as any).env?.VITE_API_BASE ?? "http://localhost:8099";

export async function getHealth() {
  return (await fetch(`${BASE}/health`)).json();
}

export async function getCustomers() {
  return (await fetch(`${BASE}/customers`)).json();
}

export async function uploadShipment(
  customerId: string,
  files: FileList,
): Promise<{ shipment_id: string }> {
  const fd = new FormData();
  fd.append("customer_id", customerId);
  Array.from(files).forEach((f) => fd.append("files", f));
  const res = await fetch(`${BASE}/shipments`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
  
  return res.json();
}

export async function getShipment(id: string) {
  const res = await fetch(`${BASE}/shipments/${id}`);
  if (!res.ok) throw new Error("shipment not found");
  return res.json();
}

export async function runQuery(question: string) {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });
  return res.json();
}
