"""M10 PDF export: a deterministic project status report (no LLM).

reportlab + the bundled Amiri font (covers Latin and Arabic). reportlab does no
shaping/bidi itself, so every string that contains Arabic is passed through
arabic_reshaper + python-bidi first. All numbers come from the same deterministic
sources the UI reads (project_card, tasks, schedule draft, risk register).
"""
import io
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Requirement, ScheduleDraft, Task, TeamMember, UserStory
from app.services.portfolio import portfolio_risks, project_card

_FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Amiri-Regular.ttf"
_FONT_BOLD_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Amiri-Bold.ttf"
pdfmetrics.registerFont(TTFont("Amiri", str(_FONT_PATH)))
pdfmetrics.registerFont(TTFont("Amiri-Bold", str(_FONT_BOLD_PATH)))

# Same palette the frontend uses, so the PDF reads like the app.
C_GREEN = colors.HexColor("#1e8a54")
C_ORANGE = colors.HexColor("#b8620b")
C_BLUE = colors.HexColor("#2f6fd1")
C_RED = colors.HexColor("#c0392b")
C_INK = colors.HexColor("#17233a")
C_SOFT = colors.HexColor("#586b83")
C_LINE = colors.HexColor("#dde5ef")
C_NAVY = colors.HexColor("#12263f")

_AR = re.compile(r"[\u0600-\u06FF]")


def _fix(s) -> str:
    """Shape + reorder Arabic for reportlab; plain Latin/digits pass through."""
    s = "" if s is None else str(s)
    if _AR.search(s):
        return get_display(arabic_reshaper.reshape(s))
    return s


def _p(text, size=9, color=C_INK, bold=False, align=None) -> Paragraph:
    style = ParagraphStyle(
        "cell", fontName="Amiri-Bold" if bold else "Amiri", fontSize=size,
        leading=size * 1.4, textColor=color, alignment=align or 0,
    )
    return Paragraph(escape(_fix(text)), style)


def _gantt_span_days(min_d: date, max_d: date) -> int:
    """Inclusive window: a task on the last day still occupies one full slot."""
    return max(1, (max_d + timedelta(days=1) - min_d).days)


def _gantt_fraction(d: date, min_d: date, max_d: date) -> float:
    """Position of ``d`` inside the inclusive window, 0 at the first day and 1 after the last."""
    return (d - min_d).days / _gantt_span_days(min_d, max_d)


def _gantt_flowable(rows, min_d, max_d, today, width=178 * mm):
    """Rows: dicts {title, start, end, status, pct, critical, overdue}. Deterministic CPM window."""
    if not rows:
        return None
    label_w = 62 * mm
    track_w = width - label_w
    row_h = 7 * mm
    h = row_h * len(rows) + 6 * mm

    class _Gantt(Flowable):
        def __init__(self):
            super().__init__()
            self.width, self.height = width, h

        def _x(self, d):
            frac = _gantt_fraction(d, min_d, max_d)
            return label_w + frac * track_w

        def draw(self):
            c = self.canv
            top = self.height
            for i, r in enumerate(rows):
                y = top - (i + 1) * row_h
                # label (indented after the overdue marker when present)
                c.setFillColor(C_INK)
                c.setFont("Amiri", 7.5)
                title = r["title"] if len(r["title"]) <= 50 else r["title"][:47] + "..."
                c.drawString(3 * mm if r["overdue"] else 0, y + 2.2 * mm, _fix(title))
                # track
                c.setFillColor(C_LINE)
                c.roundRect(label_w, y + 0.8 * mm, track_w, 4.2 * mm, 2.1 * mm, stroke=0, fill=1)
                # bar (clamped to the track — a task ending on the last day
                # must not draw past the chart edge)
                x0 = max(label_w, self._x(r["start"]))
                x1 = min(label_w + track_w, self._x(r["end"] + timedelta(days=1)))
                bw = max(2.5 * mm, x1 - x0)
                color = C_GREEN if r["status"] == "done" else C_ORANGE if r["critical"] else C_BLUE
                c.setFillColor(color)
                c.roundRect(x0, y + 0.8 * mm, bw, 4.2 * mm, 2.1 * mm, stroke=0, fill=1)
                # progress fill (white overlay strip)
                if r["pct"] > 0:
                    c.setFillColor(colors.Color(1, 1, 1, alpha=0.42))
                    c.rect(x0 + 0.4 * mm, y + 1.6 * mm, max(0, bw - 0.8 * mm) * r["pct"] / 100, 1.2 * mm, stroke=0, fill=1)
                # pct label after the bar (inside it in white when there is no room)
                c.setFont("Amiri", 6.5)
                if x1 + 11 * mm <= width:
                    c.setFillColor(C_SOFT)
                    c.drawString(x1 + 1 * mm, y + 2.2 * mm, f"{r['pct']}%")
                else:
                    c.setFillColor(colors.white)
                    c.drawRightString(x1 - 1.2 * mm, y + 2.2 * mm, f"{r['pct']}%")
                # overdue marker
                if r["overdue"]:
                    c.setFillColor(C_RED)
                    c.rect(0, y + 2.2 * mm, 1.6 * mm, 1.6 * mm, stroke=0, fill=1)
            # today line across the chart
            if min_d <= today <= max_d:
                tx = self._x(today)
                c.setStrokeColor(C_RED)
                c.setLineWidth(0.7)
                c.line(tx, 0, tx, top)
                c.setFillColor(C_RED)
                c.setFont("Amiri", 6)
                c.drawCentredString(tx, top + 1 * mm, "today")

    return _Gantt()


def build_project_pdf(db: Session, project, user) -> bytes:
    """Assemble the whole report from live database state; pure code, no LLM."""
    today = date.today()
    card = project_card(db, project, today)
    tasks = list(db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id)))
    members = {m.id: m.name for m in db.scalars(select(TeamMember).where(TeamMember.project_id == project.id))}
    risks = [r for r in portfolio_risks(db, user)["risks"] if r["project_id"] == project.id]
    schedule = db.scalar(
        select(ScheduleDraft).where(ScheduleDraft.project_id == project.id).order_by(ScheduleDraft.id.desc()).limit(1)
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"NEXUS report - {project.name}",
    )
    story: list = []

    # Header
    story.append(Paragraph(_fix("NEXUS — Project status report"), ParagraphStyle(
        "h1", fontName="Amiri", fontSize=17, leading=22, textColor=C_NAVY)))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(_fix(
        f"Project: {project.name}    ·    status: {project.status}    ·    generated: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"), ParagraphStyle(
        "meta", fontName="Amiri", fontSize=9, leading=13, textColor=C_SOFT)))
    story.append(Spacer(1, 3 * mm))

    # Summary table
    pending = (
        sum(1 for r in db.scalars(select(Requirement).where(Requirement.project_id == project.id)) if r.status == "draft")
        + sum(1 for s in db.scalars(select(UserStory).where(UserStory.project_id == project.id)) if s.review_status == "draft")
        + sum(1 for t in tasks if t.review_status == "draft")
    )
    ws, we = card["schedule"]["window_start"], card["schedule"]["window_end"]
    summary = [
        [_p("Health", bold=True), _p(card["health"]),
         _p("Progress (hours-weighted)", bold=True), _p(f"{card['progress_pct']}%")],
        [_p("Execution window", bold=True), _p(f"{ws} to {we}" if ws and we else "—"),
         _p("Next deadline", bold=True), _p(str(card.get("next_deadline") or "—"))],
        [_p("Approved tasks", bold=True), _p(str(card["tasks"]["approved"])),
         _p("Done", bold=True), _p(str(card["tasks"]["done"]))],
        [_p("Draft items pending decision", bold=True), _p(str(pending)),
         _p("Overdue tasks", bold=True), _p(str(card["overdue_tasks"]))],
        [_p("Open risks", bold=True), _p(str(len(risks))),
         _p("Health reasons", bold=True), _p(" · ".join(card["health_reasons"]) or "on track")],
    ]
    tbl = Table(summary, colWidths=[45 * mm, 45 * mm, 45 * mm, 45 * mm])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, C_LINE),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7fb")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f4f7fb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 5 * mm))

    # Gantt
    approved = [t for t in tasks if t.review_status == "approved" and t.start and t.end]
    story.append(Paragraph(_fix("Gantt — delivery tracking"), ParagraphStyle(
        "h2", fontName="Amiri", fontSize=12, leading=16, textColor=C_NAVY, spaceBefore=2 * mm)))
    if approved:
        min_d = min(t.start for t in approved)
        max_d = max(t.end for t in approved)
        rows = [{
            "title": t.title, "start": t.start, "end": t.end,
            "status": t.status, "pct": 100 if t.status == "done" else (t.progress_pct or 0),
            "critical": t.is_critical, "overdue": bool(t.end < today and t.status != "done"),
        } for t in sorted(approved, key=lambda t: (t.start, t.id))]
        story.append(_gantt_flowable(rows, min_d, max_d, today))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(_fix(
            "Bar colors: green = done, orange = critical path, blue = regular. A red dot before the label "
            "means the task is past its end date and not done; the red vertical line is today."),
            ParagraphStyle("note", fontName="Amiri", fontSize=7.5, leading=10, textColor=C_SOFT)))
    else:
        story.append(_p("No approved, dated tasks yet — approve the work plan and run Agent 3.", color=C_SOFT))

    story.append(Spacer(1, 5 * mm))

    # Tasks table
    story.append(Paragraph(_fix("Tasks"), ParagraphStyle(
        "h2b", fontName="Amiri", fontSize=12, leading=16, textColor=C_NAVY, spaceBefore=2 * mm)))
    header = [_p("#", color=colors.white, bold=True), _p("Title", color=colors.white, bold=True),
              _p("Assignee", color=colors.white, bold=True),
              _p("Start", color=colors.white, bold=True), _p("End", color=colors.white, bold=True),
              _p("Status", color=colors.white, bold=True), _p("%", color=colors.white, bold=True),
              _p("CP", color=colors.white, bold=True)]
    data = [header]
    for t in tasks:
        data.append([
            _p(str(t.id), size=8),
            _p(t.title, size=8),
            _p(members.get(t.assignee_member_id, "—") if t.assignee_member_id else "—", size=8),
            _p(str(t.start or "—"), size=8),
            _p(str(t.end or "—"), size=8),
            _p(t.review_status if t.review_status != "approved" else t.status, size=8),
            _p(str(100 if t.status == "done" else (t.progress_pct or 0)) if t.review_status == "approved" else "—", size=8),
            _p("yes" if t.is_critical else "", size=8),
        ])
    tbl2 = Table(data, colWidths=[10 * mm, 60 * mm, 28 * mm, 19 * mm, 19 * mm, 22 * mm, 10 * mm, 10 * mm], repeatRows=1)
    tbl2.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, C_LINE),
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(tbl2)
    story.append(Spacer(1, 5 * mm))

    # Risks
    story.append(Paragraph(_fix("Risks"), ParagraphStyle(
        "h2c", fontName="Amiri", fontSize=12, leading=16, textColor=C_NAVY, spaceBefore=2 * mm)))
    if risks:
        rdata = [[_p("Severity", color=colors.white, bold=True), _p("Type", color=colors.white, bold=True),
                  _p("Finding", color=colors.white, bold=True), _p("Suggested action", color=colors.white, bold=True)]]
        for r in risks:
            rdata.append([_p(r["severity"], size=8), _p(r["type"], size=8),
                          _p(r["message"], size=8), _p(r["suggestion"], size=8)])
        tbl3 = Table(rdata, colWidths=[18 * mm, 22 * mm, 76 * mm, 64 * mm], repeatRows=1)
        tbl3.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, C_LINE),
            ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(tbl3)
    else:
        story.append(_p("No open risks detected for this project.", color=C_SOFT))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(_fix(
        "Generated deterministically from the database — the agents never write progress, dates or decisions; the PM does."),
        ParagraphStyle("foot", fontName="Amiri", fontSize=7.5, leading=10, textColor=C_SOFT)))

    doc.build(story)
    return buf.getvalue()
