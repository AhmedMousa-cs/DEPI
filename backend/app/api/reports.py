"""M10 PDF export: the PM downloads a deterministic project status report.

GET /projects/{project_id}/report.pdf — PM-only, like the planning endpoints it
summarises. The bytes are built by app/services/reports.py (reportlab, no LLM).
"""
import datetime as _dt

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_role
from app.db.session import get_db
from app.services.reports import build_project_pdf

router = APIRouter(prefix="/projects/{project_id}", tags=["reports"])


@router.get("/report.pdf")
def project_report_pdf(
    project_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_role("PM")),
):
    project = get_project_or_404(db, project_id)
    pdf = build_project_pdf(db, project, user)
    filename = f"nexus-report-{project_id}-{_dt.date.today().isoformat()}.pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
