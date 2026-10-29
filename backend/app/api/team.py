import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.audit import user_actor, write_audit
from app.auth.dependencies import require_project_access, require_role
from app.config import settings
from app.db.models import ProjectMember, TeamMember, User
from app.db.session import get_db
from app.schemas.team import (
    LeaderAssign,
    TeamMemberCreate,
    TeamMemberOut,
    TeamMemberUpdate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["team"])

CSV_COLUMNS = [
    "name",
    "role_title",
    "skills",
    "capacity_hours_per_week",
    "available_from",
    "available_to",
]
MAX_IMPORT_ROWS = 200


def _j(value):
    return value.isoformat() if isinstance(value, date) else value


def _snapshot(m: TeamMember) -> dict:
    return {
        "name": m.name,
        "role_title": m.role_title,
        "skills": list(m.skills or []),
        "capacity_hours_per_week": m.capacity_hours_per_week,
        "available_from": _j(m.available_from),
        "available_to": _j(m.available_to),
        "is_leader": m.is_leader,
        "user_id": m.user_id,
    }


def _serialize(db: Session, members: list[TeamMember]) -> list[TeamMemberOut]:
    ids = {m.user_id for m in members if m.user_id}
    emails = {}
    if ids:
        emails = {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(ids)))}
    out = []
    for m in members:
        o = TeamMemberOut.model_validate(m)
        o.user_email = emails.get(m.user_id)
        out.append(o)
    return out


def _get_member(db: Session, project_id: int, member_id: int) -> TeamMember:
    m = db.get(TeamMember, member_id)
    if m is None or m.project_id != project_id:
        raise HTTPException(404, "Team member not found")
    return m


def _name_taken(db: Session, project_id: int, name: str, exclude_id: int | None = None):
    stmt = select(TeamMember.id).where(
        TeamMember.project_id == project_id,
        func.lower(TeamMember.name) == name.lower(),
    )
    if exclude_id is not None:
        stmt = stmt.where(TeamMember.id != exclude_id)
    return db.scalar(stmt) is not None


def _unlink_user(db: Session, project_id: int, user_id: int) -> None:
    link = db.get(ProjectMember, (project_id, user_id))
    if link is not None:
        db.delete(link)


@router.get("/team", response_model=list[TeamMemberOut])
def list_team(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_access),
):
    get_project_or_404(db, project_id)
    members = list(
        db.scalars(
            select(TeamMember)
            .where(TeamMember.project_id == project_id)
            .order_by(TeamMember.id)
        )
    )
    return _serialize(db, members)


@router.post("/team", response_model=TeamMemberOut, status_code=201)
def add_member(
    project_id: int,
    body: TeamMemberCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    if _name_taken(db, project_id, body.name):
        raise HTTPException(409, f"A team member named '{body.name}' already exists")
    m = TeamMember(project_id=project_id, **body.model_dump())
    db.add(m)
    db.flush()
    write_audit(
        db, user_actor(user), "team.create", "team_member", m.id, after=_snapshot(m)
    )
    db.commit()
    db.refresh(m)
    return _serialize(db, [m])[0]


@router.post("/team/import", response_model=list[TeamMemberOut], status_code=201)
def import_team_csv(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(415, "Only .csv files are supported for team import.")
    limit = settings.max_upload_bytes
    raw = file.file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(413, "CSV file is too large.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV must be UTF-8 encoded.")

    reader = csv.DictReader(io.StringIO(text))
    headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    if not headers:
        raise HTTPException(422, "The CSV file is empty.")
    reader.fieldnames = headers
    unknown = [h for h in headers if h not in CSV_COLUMNS]
    if "name" not in headers or unknown:
        raise HTTPException(
            422,
            {
                "message": "Invalid CSV header. Expected columns: "
                + ", ".join(CSV_COLUMNS)
                + " ('name' is required)."
                + (f" Unknown columns: {', '.join(unknown)}." if unknown else ""),
                "errors": [],
            },
        )

    existing = {
        n.lower()
        for n in db.scalars(select(TeamMember.name).where(TeamMember.project_id == project_id))
    }
    seen: set[str] = set()
    valid: list[TeamMemberCreate] = []
    errors: list[dict] = []
    for line_no, row in enumerate(reader, start=2):
        if None in row:
            errors.append({"line": line_no, "message": "too many columns in this row"})
            continue
        if not any((v or "").strip() for v in row.values()):
            continue
        if len(valid) + len(errors) >= MAX_IMPORT_ROWS:
            raise HTTPException(422, f"Too many rows (max {MAX_IMPORT_ROWS}).")
        payload: dict = {}
        for key in CSV_COLUMNS:
            cell = (row.get(key) or "").strip()
            if cell:
                payload[key] = cell.split(";") if key == "skills" else cell
        try:
            member = TeamMemberCreate.model_validate(payload)
        except ValidationError as e:
            msg = "; ".join(
                (f"{'.'.join(str(p) for p in x['loc'])}: " if x["loc"] else "") + x["msg"]
                for x in e.errors()
            )
            errors.append({"line": line_no, "message": msg})
            continue
        key = member.name.lower()
        if key in existing or key in seen:
            errors.append({"line": line_no, "message": f"duplicate name '{member.name}'"})
            continue
        seen.add(key)
        valid.append(member)

    if errors:
        raise HTTPException(
            422,
            {
                "message": "CSV import rejected: no rows were saved.",
                "errors": errors[:50],
            },
        )
    if not valid:
        raise HTTPException(422, "The CSV has no data rows.")

    created = [TeamMember(project_id=project_id, **v.model_dump()) for v in valid]
    db.add_all(created)
    db.flush()
    write_audit(
        db,
        user_actor(user),
        "team.import",
        "project",
        project_id,
        after={"count": len(created), "names": [m.name for m in created]},
    )
    db.commit()
    for m in created:
        db.refresh(m)
    return _serialize(db, created)


@router.patch("/team/{member_id}", response_model=TeamMemberOut)
def update_member(
    project_id: int,
    member_id: int,
    body: TeamMemberUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    m = _get_member(db, project_id, member_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "No fields to update")
    if "name" in data and _name_taken(db, project_id, data["name"], exclude_id=m.id):
        raise HTTPException(409, f"A team member named '{data['name']}' already exists")

    before: dict = {}
    after: dict = {}
    for field, value in data.items():
        old = getattr(m, field)
        if field == "skills":
            old = list(old or [])
        if old != value:
            before[field] = _j(old)
            after[field] = _j(value)
            setattr(m, field, value)
    if m.available_from and m.available_to and m.available_to < m.available_from:
        raise HTTPException(422, "available_to must not be before available_from")
    if before or after:
        write_audit(
            db,
            user_actor(user),
            "team.update",
            "team_member",
            m.id,
            before=before,
            after=after,
        )
        db.commit()
        db.refresh(m)
    return _serialize(db, [m])[0]


@router.delete("/team/{member_id}", status_code=204)
def delete_member(
    project_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    m = _get_member(db, project_id, member_id)
    before = _snapshot(m)
    if m.user_id:
        _unlink_user(db, project_id, m.user_id)
    db.delete(m)
    write_audit(
        db, user_actor(user), "team.delete", "team_member", member_id, before=before
    )
    db.commit()
    return Response(status_code=204)


@router.put("/leader", response_model=TeamMemberOut)
def set_leader(
    project_id: int,
    body: LeaderAssign,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    member = _get_member(db, project_id, body.member_id)
    leader = db.scalar(select(User).where(User.email == body.user_email.strip().lower()))
    if leader is None:
        raise HTTPException(404, "No user with this email")
    if leader.role != "TEAM_LEADER":
        raise HTTPException(422, "The selected user must have the TEAM_LEADER role")
    clash = db.scalar(
        select(TeamMember).where(
            TeamMember.project_id == project_id,
            TeamMember.user_id == leader.id,
            TeamMember.id != member.id,
        )
    )
    if clash is not None:
        raise HTTPException(
            409, f"This user is already linked to team member '{clash.name}'"
        )

    before = _snapshot(member)
    previous = db.scalars(
        select(TeamMember).where(
            TeamMember.project_id == project_id,
            TeamMember.is_leader.is_(True),
            TeamMember.id != member.id,
        )
    )
    for prev in previous:
        if prev.user_id:
            _unlink_user(db, project_id, prev.user_id)
        prev.is_leader = False
        prev.user_id = None
    if member.user_id and member.user_id != leader.id:
        _unlink_user(db, project_id, member.user_id)
    member.user_id = leader.id
    member.is_leader = True
    if db.get(ProjectMember, (project_id, leader.id)) is None:
        db.add(ProjectMember(project_id=project_id, user_id=leader.id))
    write_audit(
        db,
        user_actor(user),
        "team.set_leader",
        "team_member",
        member.id,
        before=before,
        after=_snapshot(member),
    )
    db.commit()
    db.refresh(member)
    return _serialize(db, [member])[0]


@router.delete("/leader", status_code=204)
def remove_leader(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    member = db.scalar(
        select(TeamMember).where(
            TeamMember.project_id == project_id, TeamMember.is_leader.is_(True)
        )
    )
    if member is None:
        raise HTTPException(404, "This project has no Team Leader")
    before = _snapshot(member)
    if member.user_id:
        _unlink_user(db, project_id, member.user_id)
    member.is_leader = False
    member.user_id = None
    write_audit(
        db,
        user_actor(user),
        "team.remove_leader",
        "team_member",
        member.id,
        before=before,
        after=_snapshot(member),
    )
    db.commit()
    return Response(status_code=204)
