"""Coverage-gap check (M4.1): which standard product areas did nobody mention?

Two layers:
* deterministic keyword match (`coverage_gaps`) — instant, free, but word-based (a "school app"
  whose requirement says "upload grades" matches "load"; that is a known false positive);
* LLM-judged analysis (`run_llm_gap_analysis`) — meaning-based, stored as a CoverageReport,
  wired through run_structured (budget / agent_runs / audit / Langfuse). The LLM verdict is
  sanitised by code: unknown areas are dropped, matched_ids are filtered to real requirement
  ids, and every missing area still gets a deterministic row as fallback.

A "missing" area is a review-meeting question, not a fact — the PM decides the scope.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import CoverageReport, Requirement
from app.llm.runner import run_structured
from app.schemas.extraction import GapAnalysisOutput
from app.services.quotes import normalize


@dataclass(frozen=True)
class _Area:
    name: str
    keywords: tuple[str, ...]
    brief: str


_AREAS: tuple[_Area, ...] = (
    _Area("Authentication & accounts", (
        "login", "log in", "sign in", "sign up", "register", "registration", "password",
        "account", "authentication", "auth", "تسجيل", "دخول", "حساب", "كلمة المرور", "كلمة السر", "مصادقة",
    ), "who can enter the system and how"),
    _Area("Roles & permissions", (
        "role", "permission", "access control", "admin", "administrator", "privilege",
        "صلاحية", "صلاحيات", "دور", "مدير", "مسؤول", "أدمن",
    ), "who is allowed to do what"),
    _Area("Data & reporting", (
        "report", "reports", "dashboard", "analytics", "chart", "statistics", "export",
        "تقارير", "تقرير", "لوحة", "إحصائيات", "تحليل", "تصدير",
    ), "what the business sees about its own data"),
    _Area("Notifications & communication", (
        "notification", "notifications", "email", "e-mail", "sms", "alert", "reminder",
        "message", "chat", "إشعار", "إشعارات", "تنبيه", "تنبيهات", "بريد", "رسائل", "تذكير",
    ), "how the system proactively reaches people"),
    _Area("Payments & billing", (
        "payment", "payments", "pay", "card", "checkout", "invoice", "refund", "billing",
        "دفع", "فاتورة", "فواتير", "مدفوعات", "استرداد", "كاش",
    ), "money in, receipts and refunds"),
    _Area("Search & navigation", (
        "search", "filter", "filters", "sort", "browse", "navigate", "navigation",
        "بحث", "تصفية", "فلتر", "فرز", "تصفح", "تنقل",
    ), "how users find things"),
    _Area("Integrations & APIs", (
        "integration", "api", "third-party", "third party", "webhook", "import", "sync",
        "تكامل", "ربط", "استيراد", "مزامنة",
    ), "connections to other systems"),
    _Area("Performance & scale", (
        "performance", "load", "speed", "concurrent", "scalable", "scalability",
        "response time", "أداء", "سرعة", "حمل", "ضغط", "متزامن",
    ), "speed and number of users"),
    _Area("Security & privacy", (
        "security", "privacy", "encryption", "encrypt", "https", "gdpr", "data protection",
        "audit", "أمان", "خصوصية", "تشفير", "حماية", "حماية البيانات",
    ), "protecting accounts and sensitive data"),
    _Area("Devices & platforms", (
        "mobile", "responsive", "web app", "device", "devices", "offline", "android", "ios",
        "browser", "موبايل", "موبايلات", "جهاز", "أجهزة", "أوفلاين", "تطبيق",
    ), "where the product must run"),
    _Area("Backup & availability", (
        "backup", "restore", "availability", "uptime", "disaster recovery", "downtime",
        "نسخ احتياطي", "استرجاع", "استعادة", "تعطل", "إتاحة",
    ), "surviving failures and data loss"),
)

def _haystack(r: Requirement) -> str:
    return normalize(f"{r.text} {r.quote or ''}")


def coverage_gaps(requirements: list[Requirement]) -> list[dict]:
    """One row per standard area: covered / missing with the requirement ids that matched.

    Input: the project's requirements (usually every status except rejected).
    """
    haystacks = [(r.id, _haystack(r)) for r in requirements]
    rows: list[dict] = []
    for area in _AREAS:
        matched: list[int] = []
        for rid, hay in haystacks:
            if any(k in hay for k in area.keywords):
                matched.append(rid)
        rows.append({
            "area": area.name,
            "covered": bool(matched),
            "matched_ids": matched,
            "source": "keywords",
        })
    return rows


def gaps_summary(rows: list[dict]) -> dict:
    missing = [row["area"] for row in rows if not row["covered"]]
    return {
        "areas": rows,
        "covered_count": len(rows) - len(missing),
        "missing_count": len(missing),
        "missing": missing,
    }


def project_gaps(db: Session, project_id: int) -> dict:
    """Gap check over the project's live requirements (rejected ones are excluded)."""
    rows = list(db.scalars(
        select(Requirement).where(
            Requirement.project_id == project_id, Requirement.status != "rejected"
        ).order_by(Requirement.id)
    ))
    if not rows:
        return {"areas": [], "covered_count": 0, "missing_count": 0, "missing": [], "empty": True}
    return {**gaps_summary(coverage_gaps(rows)), "empty": False}


# ------------------------------------------------------------------ LLM-judged analysis (M4.1)
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "agents" / "prompts" / "agent1_gaps_v1.md"
AGENT_NAME = "agent1_gaps"
PROMPT_VERSION = "agent1_gaps_v1"


def _llm_payload(requirements: list[Requirement]) -> dict:
    """Data for the LLM: the fixed areas + the requirement texts (capped; quotes stay out)."""
    return {
        "areas": [{"name": a.name, "covers": a.brief} for a in _AREAS],
        "requirements": [
            {"id": r.id, "type": r.type, "text": (r.text or "")[:300]}
            for r in requirements
        ],
    }


def run_llm_gap_analysis(db: Session, project_id: int, *, job_id: int | None = None) -> dict:
    """LLM gap analysis, sanitised by code and persisted as the latest CoverageReport.

    Returns the combined API shape: deterministic rows + the LLM verdict per area.
    """
    rows = list(db.scalars(
        select(Requirement).where(
            Requirement.project_id == project_id, Requirement.status != "rejected"
        ).order_by(Requirement.id)
    ))
    if not rows:
        raise ValueError("run the extraction first")

    messages = [
        {"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8")},
        # payload is untrusted data; the prompt says so explicitly
        {"role": "user", "content": __import__("json").dumps(_llm_payload(rows), ensure_ascii=False)},
    ]
    run = run_structured(
        project_id=project_id,
        job_id=job_id,
        agent=AGENT_NAME,
        prompt_version=PROMPT_VERSION,
        messages=messages,
        schema=GapAnalysisOutput,
    )
    parsed: GapAnalysisOutput = run.parsed

    # --- sanitise: the LLM only narrows/annotates the deterministic rows, never invents them
    real_ids = {r.id for r in rows}
    known = {a.name: a for a in _AREAS}
    by_area: dict[str, dict] = {}
    for a in parsed.assessments:
        if a.area not in known:  # an invented area is dropped, not shown
            continue
        by_area[a.area] = {
            "area": a.area,
            "covered": bool(a.covered),
            "matched_ids": [i for i in a.matched_ids if i in real_ids],  # hallucinated ids never pass
            "note": (a.note or "")[:200],
        }

    keyword_rows = {row["area"]: row for row in coverage_gaps(rows)}
    areas_out = []
    for a in _AREAS:
        llm = by_area.get(a.name)
        row = keyword_rows[a.name]
        areas_out.append({
            "area": a.name,
            "covered": llm["covered"] if llm else row["covered"],
            "matched_ids": llm["matched_ids"] if llm else row["matched_ids"],
            "note": llm["note"] if llm else "",
            "source": "llm" if llm else "keywords",
        })
    missing = [row["area"] for row in areas_out if not row["covered"]]

    # stored shape: data + meta. The API 'llm' block is composed on read, never nested here.
    meta = {
        "run_id": run.run_id,
        "prompt_version": PROMPT_VERSION,
        "analyzed_requirements": len(rows),
    }
    payload = {
        "areas": areas_out,
        "covered_count": len(areas_out) - len(missing),
        "missing_count": len(missing),
        "missing": missing,
        "empty": False,
        "meta": meta,
    }

    report = CoverageReport(
        project_id=project_id,
        payload=payload,
        run_id=run.run_id,
        created_by_agent=AGENT_NAME,
    )
    db.add(report)
    write_audit(
        db,
        f"agent:{AGENT_NAME}",
        "agent1.gaps_analyzed",
        "project",
        project_id,
        after={
            "run_id": run.run_id,
            "covered": payload["covered_count"],
            "missing": payload["missing_count"],
            "dropped_areas": len(parsed.assessments) - len(by_area),
        },
    )
    return {**payload, "llm": {**meta, "created_at": datetime.now(timezone.utc).isoformat()}}


def latest_gap_payload(db: Session, project_id: int) -> dict | None:
    """The newest stored analysis (data + meta), or None."""
    row = db.scalar(
        select(CoverageReport)
        .where(CoverageReport.project_id == project_id)
        .order_by(CoverageReport.id.desc())
        .limit(1)
    )
    return row.payload if row else None


def latest_gap_report(db: Session, project_id: int) -> dict | None:
    """The stored analysis composed into the API's `llm` block, or None."""
    row = db.scalar(
        select(CoverageReport)
        .where(CoverageReport.project_id == project_id)
        .order_by(CoverageReport.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    meta = dict((row.payload or {}).get("meta", {}))
    if row.created_at is not None:
        meta["created_at"] = row.created_at.isoformat()
    return meta
