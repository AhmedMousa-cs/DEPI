from app.db.models import User, Project

def test_db_connection_and_user_creation(db_session):
    user = User(
        name="Test PM",
        email="test_pm@nexus.ai",
        password_hash="hashed_secret_123",
        role="PM"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.id is not None
    assert user.role == "PM"
    assert user.email == "test_pm@nexus.ai"
    assert user.name == "Test PM"

def test_project_model_structure(db_session):
    user = User(
        name="Project Owner",
        email="owner@nexus.ai",
        password_hash="hash",
        role="PM"
    )
    db_session.add(user)
    db_session.commit()

    project = Project(
        name="NEXUS Core",
        owner_id=user.id
    )
    db_session.add(project)
    db_session.commit()

    assert project.id is not None
    assert project.owner_id == user.id
