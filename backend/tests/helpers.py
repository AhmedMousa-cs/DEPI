import io

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.auth.security import hash_password
from app.db.models import AuditLog, ProjectMember, User
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)

PM = ("pm@nexus.test", "pm12345")
TL = ("leader@nexus.test", "leader12345")


def login(email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def pm_headers():
    return login(*PM)


def tl_headers():
    return login(*TL)


def make_project(headers, **override) -> dict:
    body = {
        "name": "Clinic Booking",
        "goal": "Online appointments",
        "start_date": "2026-11-01",
        "end_date": "2026-12-01",
        "brief": "Patients book clinic appointments online.",
    }
    body.update(override)
    r = client.post("/projects", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def ensure_user(email, name, role, password):
    with SessionLocal() as s:
        if s.scalar(select(User).where(User.email == email)) is None:
            s.add(
                User(
                    email=email,
                    name=name,
                    role=role,
                    password_hash=hash_password(password),
                )
            )
            s.commit()


def assign_tl(project_id, email=TL[0]):
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email))
        s.add(ProjectMember(project_id=project_id, user_id=u.id))
        s.commit()


def audit_rows(action):
    with SessionLocal() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


def make_docx(text: str) -> bytes:
    d = Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_pdf(text: str) -> bytes:
    """Minimal valid one-page PDF with real extractable text."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref,
    )
    return bytes(out)


def make_blank_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()
