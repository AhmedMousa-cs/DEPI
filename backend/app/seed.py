from __future__ import annotations

from sqlalchemy import select

from app.auth.security import hash_password
from app.db.models import User
from app.db.session import SessionLocal


SEED_USERS = (
    {
        "email": "pm@nexus.test",
        "name": "Demo PM",
        "role": "PM",
        "password": "pm12345",
    },
    {
        "email": "leader@nexus.test",
        "name": "Demo Leader",
        "role": "TEAM_LEADER",
        "password": "leader12345",
    },
)


def seed() -> None:
    """
    Insert deterministic demo users if they do not already exist.

    This function is intentionally idempotent and commits only its own
    seed transaction.
    """
    with SessionLocal() as db:
        for user_data in SEED_USERS:
            exists = db.scalar(
                select(User).where(
                    User.email == user_data["email"],
                )
            )

            if exists is not None:
                continue

            db.add(
                User(
                    email=user_data["email"],
                    name=user_data["name"],
                    role=user_data["role"],
                    password_hash=hash_password(user_data["password"]),
                )
            )

        db.commit()


if __name__ == "__main__":
    seed()
