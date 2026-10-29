from pathlib import PurePosixPath
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.audit import user_actor, write_audit
from app.auth.dependencies import require_project_access, require_role
from app.config import settings
from app.db.models import Source, User
from app.db.session import get_db
from app.ingest.extractors import IngestError, clean_text, extract_text
from app.schemas.source import SourceDetail, SourceOut

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])

ALLOWED_KINDS = {"transcript", "note", "file"}
INITIAL_STATUSES = {"setup", "planning", "scope_review", "plan_review"}


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:g} MB"


def _safe_filename(name: str) -> str | None:
    base = PurePosixPath(name.replace("\\", "/")).name
    base = "".join(ch for ch in base if ch.isprintable())
    return base[:255] or None


def _out(s: Source) -> SourceOut:
    return SourceOut(
        id=s.id,
        project_id=s.project_id,
        kind=s.kind,
        title=s.title,
        original_filename=s.original_filename,
        phase=s.phase,
        created_at=s.created_at,
        content_length=len(s.content_text),
    )


@router.post("", response_model=SourceOut, status_code=201)
def create_source(
    project_id: int,
    kind: str = Form("transcript"),
    title: str | None = Form(None),
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    project = get_project_or_404(db, project_id)
    if kind not in ALLOWED_KINDS:
        raise HTTPException(422, f"kind must be one of: {', '.join(sorted(ALLOWED_KINDS))}")

    has_file = file is not None and bool(file.filename)
    has_text = text is not None and text.strip() != ""
    if has_file == has_text:
        raise HTTPException(422, "Provide exactly one of: pasted text or a file.")

    limit = settings.max_upload_bytes
    filename = None
    if has_file:
        data = file.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File is too large (max {_mb(limit)}).")
        filename = _safe_filename(file.filename)
        try:
            content = extract_text(filename or "", data)
        except IngestError as e:
            raise HTTPException(e.status_code, e.message)
    else:
        content = clean_text(text)
        if not content:
            raise HTTPException(422, "The text contains no usable characters.")
        if len(content.encode("utf-8")) > limit:
            raise HTTPException(413, f"Text is too large (max {_mb(limit)}).")

    source = Source(
        project_id=project.id,
        kind=kind,
        title=((title or "").strip()[:255]) or filename or f"Pasted {kind}",
        content_text=content,
        original_filename=filename,
        uploaded_by=user.id,
        phase="initial" if project.status in INITIAL_STATUSES else "execution",
    )
    db.add(source)
    db.flush()
    write_audit(
        db,
        user_actor(user),
        "source.create",
        "source",
        source.id,
        after={
            "project_id": project.id,
            "kind": source.kind,
            "title": source.title,
            "original_filename": source.original_filename,
            "content_length": len(content),
            "phase": source.phase,
        },
    )
    db.commit()
    db.refresh(source)
    return _out(source)


@router.get("", response_model=list[SourceOut])
def list_sources(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_access),
):
    get_project_or_404(db, project_id)
    rows = db.scalars(
        select(Source).where(Source.project_id == project_id).order_by(Source.id)
    )
    return [_out(s) for s in rows]


@router.get("/{source_id}", response_model=SourceDetail)
def get_source(
    project_id: int,
    source_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_access),
):
    source = db.get(Source, source_id)
    if source is None or source.project_id != project_id:
        raise HTTPException(404, "Source not found")
    return SourceDetail(**_out(source).model_dump(), content_text=source.content_text)
