"""M10 PDF export: GET /projects/{pid}/report.pdf builds a deterministic status report.

PM-only, real application/pdf bytes starting with %PDF, a download filename, and
at least one page; Arabic project names must not break shaping/bidi.
"""
from pypdf import PdfReader

from helpers import client, make_project, pm_headers, tl_headers


def test_pm_downloads_a_real_pdf():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    r = client.get(f"/projects/{pid}/report.pdf", headers=pm)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers["content-disposition"]
    assert f"nexus-report-{pid}" in r.headers["content-disposition"]
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 4000  # fonts + tables, not an empty stub

    reader = PdfReader(__import__("io").BytesIO(r.content))
    assert len(reader.pages) >= 1
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    assert "Project status report" in text
    assert "Tasks" in text


def test_arabic_project_name_does_not_break_the_pdf():
    pm = pm_headers()
    pid = make_project(pm, name="مدرسه — تجربة", goal="هدف تجربة")["id"]
    r = client.get(f"/projects/{pid}/report.pdf", headers=pm)
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
    reader = PdfReader(__import__("io").BytesIO(r.content))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    assert "Project status report" in text  # Latin labels unaffected


def test_team_leader_cannot_download_the_report():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    r = client.get(f"/projects/{pid}/report.pdf", headers=tl_headers())
    assert r.status_code == 403


def test_unknown_project_is_404():
    r = client.get("/projects/999999/report.pdf", headers=pm_headers())
    assert r.status_code == 404
