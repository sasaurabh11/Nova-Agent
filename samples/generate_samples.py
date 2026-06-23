"""
Creates (reproducibly):
  clean/commercial_invoice_acme.pdf    -> all fields satisfy ACME's rules
  clean/bill_of_lading_acme.pdf         -> all fields satisfy ACME's rules
  clean/commercial_invoice_mismatch.pdf -> readable but violates two rules
  messy/commercial_invoice_scan.png     -> degraded scan (low-quality input)
"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
CLEAN = ROOT / "clean"
MESSY = ROOT / "messy"
EMAILS = ROOT / "emails"   # Part 2: SU email bundles (multi-doc shipments)

FIELD_LABELS = {
    "consignee_name": "Consignee",
    "hs_code": "HS Code",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "incoterms": "Incoterms",
    "description_of_goods": "Description of Goods",
    "gross_weight": "Gross Weight",
    "invoice_number": "Invoice Number",
}

CLEAN_INVOICE = {
    "consignee_name": "ACME Imports GmbH",
    "hs_code": "8471.30",
    "port_of_loading": "Shanghai",
    "port_of_discharge": "Hamburg",
    "incoterms": "FOB",
    "description_of_goods": "Portable computing devices (laptops)",
    "gross_weight": "1240 kg",
    "invoice_number": "INV-2026-0412",
}
CLEAN_BOL = {**CLEAN_INVOICE, "description_of_goods": "Laptops, 120 cartons"}
# Readable but violates two rules: EXW not allowed, discharge must be Hamburg.
MISMATCH_INVOICE = {
    **CLEAN_INVOICE,
    "incoterms": "EXW",
    "port_of_discharge": "Rotterdam",
    "invoice_number": "INV-2026-0419",
}
# Clean & readable, but the supplier left out gross weight AND invoice number.
# Those come back not_found -> uncertain -> the Router flags it for human review
# (no rule is violated, so it is NOT an amendment). A real "SU forgot a field" case.
INCOMPLETE_INVOICE = {
    k: v for k, v in CLEAN_INVOICE.items()
    if k not in ("gross_weight", "invoice_number")
}
# Part 2 multi-doc shipment: a packing list consistent with the invoice/BOL.
PACKING_LIST = {**CLEAN_INVOICE, "description_of_goods": "Laptops — 120 cartons (10 units/carton)"}
# Each doc is individually VALID, but this BOL's HS code disagrees with the invoice's
# (8471.30) — a cross-document conflict that only multi-doc validation can catch.
CONFLICT_BOL = {**CLEAN_BOL, "hs_code": "8528.51"}


def _draw(c: canvas.Canvas, title: str, values: dict) -> None:
    w, h = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(25 * mm, h - 30 * mm, title)
    c.setFont("Helvetica", 9)
    c.drawString(25 * mm, h - 38 * mm, "ACME Imports GmbH  ·  Musterstrasse 1, Hamburg, Germany")
    c.line(25 * mm, h - 42 * mm, w - 25 * mm, h - 42 * mm)
    y = h - 55 * mm
    for key in FIELD_LABELS:
        if key not in values:
            continue
        c.setFont("Helvetica-Bold", 11)
        c.drawString(25 * mm, y, f"{FIELD_LABELS[key]}:")
        c.setFont("Helvetica", 11)
        c.drawString(80 * mm, y, str(values[key]))
        y -= 11 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(25 * mm, 20 * mm, "Sample document generated for the Nova trade pipeline POC.")
    c.showPage()
    c.save()


def make_pdf(values: dict, title: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _draw(c, title, values)
    return buf.getvalue()


def degrade_to_png(pdf_bytes: bytes) -> bytes:
    """Render PDF -> image, then simulate a poor scan (downscale, skew, noise, JPEG)."""
    import fitz
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=110)
    doc.close()
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")  # grayscale scan
    small = img.resize((img.width // 3, img.height // 3))          # lose detail
    img = small.resize(img.size)
    img = img.rotate(1.5, expand=False, fillcolor=255)             # slight skew
    px = img.load()                                                # fixed noise pattern
    for y in range(0, img.height, 7):
        for x in range(0, img.width, 11):
            px[x, y] = 0 if (x + y) % 3 == 0 else 255
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=22)        # heavy compression
    Image.open(io.BytesIO(out.getvalue())).save(out2 := io.BytesIO(), format="PNG")
    return out2.getvalue()


def write_bundle(name: str, subject: str, docs: list[tuple[str, bytes]]) -> None:
    """Write one SU shipment's documents — the set you attach to a test email (or
    feed via POST /inbox/emails) to trigger the pipeline."""
    d = EMAILS / name
    d.mkdir(parents=True, exist_ok=True)
    for filename, data in docs:
        (d / filename).write_bytes(data)


def main() -> None:
    CLEAN.mkdir(parents=True, exist_ok=True)
    MESSY.mkdir(parents=True, exist_ok=True)

    inv = make_pdf(CLEAN_INVOICE, "COMMERCIAL INVOICE")
    (CLEAN / "commercial_invoice_acme.pdf").write_bytes(inv)
    (CLEAN / "bill_of_lading_acme.pdf").write_bytes(make_pdf(CLEAN_BOL, "BILL OF LADING"))
    (CLEAN / "commercial_invoice_mismatch.pdf").write_bytes(make_pdf(MISMATCH_INVOICE, "COMMERCIAL INVOICE"))
    (CLEAN / "commercial_invoice_incomplete.pdf").write_bytes(make_pdf(INCOMPLETE_INVOICE, "COMMERCIAL INVOICE"))
    (MESSY / "commercial_invoice_scan.png").write_bytes(degrade_to_png(inv))

    # Part 2: multi-document SU email bundles.
    write_bundle("clean_shipment", "Shipment ACME-1001 — documents for approval", [
        ("commercial_invoice.pdf", make_pdf(CLEAN_INVOICE, "COMMERCIAL INVOICE")),
        ("bill_of_lading.pdf", make_pdf(CLEAN_BOL, "BILL OF LADING")),
        ("packing_list.pdf", make_pdf(PACKING_LIST, "PACKING LIST")),
    ])
    write_bundle("conflict_shipment", "Shipment ACME-1002 — documents for approval", [
        ("commercial_invoice.pdf", make_pdf(CLEAN_INVOICE, "COMMERCIAL INVOICE")),   # HS 8471.30
        ("bill_of_lading.pdf", make_pdf(CONFLICT_BOL, "BILL OF LADING")),            # HS 8528.51 (conflict)
        ("packing_list.pdf", make_pdf(PACKING_LIST, "PACKING LIST")),
    ])

    print("Generated sample documents:")
    for p in sorted(CLEAN.glob("*.pdf")) + sorted(MESSY.glob("*.png")):
        print(f"  - {p.relative_to(ROOT.parent)}")
    print("Generated Part 2 email bundles:")
    for d in sorted(EMAILS.iterdir()):
        if d.is_dir():
            print(f"  - {d.relative_to(ROOT.parent)}/  ({len(list(d.glob('*.pdf')))} docs)")


if __name__ == "__main__":
    main()
