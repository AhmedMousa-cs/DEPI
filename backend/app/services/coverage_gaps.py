"""Deterministic coverage-gap check (M4.1): which standard product areas did nobody mention?

No LLM: each area is a fixed list of EN/AR keywords matched (substring, normalized) against the
requirement text + its quote. A "missing" area is a review-meeting question, not a fact — the PM
decides whether it is really out of scope.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Requirement
from app.services.quotes import normalize


@dataclass(frozen=True)
class _Area:
    name: str
    keywords: tuple[str, ...]


_AREAS: tuple[_Area, ...] = (
    _Area("Authentication & accounts", (
        "login", "log in", "sign in", "sign up", "register", "registration", "password",
        "account", "authentication", "auth", "تسجيل", "دخول", "حساب", "كلمة المرور", "كلمة السر", "مصادقة",
    )),
    _Area("Roles & permissions", (
        "role", "permission", "access control", "admin", "administrator", "privilege",
        "صلاحية", "صلاحيات", "دور", "مدير", "مسؤول", "أدمن",
    )),
    _Area("Data & reporting", (
        "report", "reports", "dashboard", "analytics", "chart", "statistics", "export",
        "تقارير", "تقرير", "لوحة", "إحصائيات", "تحليل", "تصدير",
    )),
    _Area("Notifications & communication", (
        "notification", "notifications", "email", "e-mail", "sms", "alert", "reminder",
        "message", "chat", "إشعار", "إشعارات", "تنبيه", "تنبيهات", "بريد", "رسائل", "تذكير",
    )),
    _Area("Payments & billing", (
        "payment", "payments", "pay", "card", "checkout", "invoice", "refund", "billing",
        "دفع", "فاتورة", "فواتير", "مدفوعات", "استرداد", "كاش",
    )),
    _Area("Search & navigation", (
        "search", "filter", "filters", "sort", "browse", "navigate", "navigation",
        "بحث", "تصفية", "فلتر", "فرز", "تصفح", "تنقل",
    )),
    _Area("Integrations & APIs", (
        "integration", "api", "third-party", "third party", "webhook", "import", "sync",
        "تكامل", "ربط", "استيراد", "مزامنة",
    )),
    _Area("Performance & scale", (
        "performance", "load", "speed", "concurrent", "scalable", "scalability",
        "response time", "أداء", "سرعة", "حمل", "ضغط", "متزامن",
    )),
    _Area("Security & privacy", (
        "security", "privacy", "encryption", "encrypt", "https", "gdpr", "data protection",
        "audit", "أمان", "خصوصية", "تشفير", "حماية", "حماية البيانات",
    )),
    _Area("Devices & platforms", (
        "mobile", "responsive", "web app", "device", "devices", "offline", "android", "ios",
        "browser", "موبايل", "موبايلات", "جهاز", "أجهزة", "أوفلاين", "تطبيق",
    )),
    _Area("Backup & availability", (
        "backup", "restore", "availability", "uptime", "disaster recovery", "downtime",
        "نسخ احتياطي", "استرجاع", "استعادة", "تعطل", "إتاحة",
    )),
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
