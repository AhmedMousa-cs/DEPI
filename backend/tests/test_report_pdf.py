"""M10 PDF export: GET /projects/{pid}/report.pdf builds a deterministic status report.

PM-only, real application/pdf bytes starting with %PDF, a download filename, and
at least one page; Arabic project names must not break shaping/bidi.
"""
from datetime import date

from pypdf import PdfReader

from app.services.reports import _gantt_fraction, _gantt_span_days
from helpers import client, make_project, pm_headers, tl_headers


def test_gantt_geometry_counts_the_last_day():
    """End dates are inclusive: two one-day tasks must fill two slots, not one."""
    start, end = date(2026, 11, 1), date(2026, 11, 2)
    assert _gantt_span_days(start, end) == 2
    assert _gantt_fraction(start, start, end) == 0.0
    assert _gantt_fraction(date(2026, 11, 2), start, end) == 0.5  # second task starts halfway
    assert _gantt_fraction(date(2026, 11, 3), start, end) == 1.0  # exclusive right edge


def test_gantt_geometry_keeps_the_working_day_window_intact():
    # Golden C: Nov 16 .. Dec 4 (15 working days inside a 19-day calendar window).
    start, end = date(2026, 11, 16), date(2026, 12, 4)
    assert _gantt_span_days(start, end) == 19
    assert _gantt_fraction(date(2026, 11, 17), start, end) == 1 / 19
    assert _gantt_fraction(date(2026, 12, 4), start, end) == 18 / 19  # the last day is not zero-width


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
