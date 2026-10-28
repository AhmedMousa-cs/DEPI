from sqlalchemy import select

from app.auth.security import hash_password
from app.db.models import User
from app.db.session import SessionLocal

SEED_USERS = [
    {"email": "pm@nexus.test", "name": "Demo PM", "role": "PM", "password": "pm12345"},
    {
        "email": "leader@nexus.test",
        "name": "Demo Leader",
        "role": "TEAM_LEADER",
        "password": "leader12345",
    },
]


def seed() -> None:
    with SessionLocal() as db:
        for u in SEED_USERS:
            exists = db.scalar(select(User).where(User.email == u["email"]))
            if exists:
                continue
            db.add(
                User(
                    email=u["email"],
                    name=u["name"],
                    role=u["role"],
                    password_hash=hash_password(u["password"]),
                )
            )
        db.commit()
    print("seed done")


if __name__ == "__main__":
    seed()