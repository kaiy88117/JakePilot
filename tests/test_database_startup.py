from db.base.session_manager import SessionManager
from config.database import DatabaseConfig


def test_sqlite_parent_directory_is_created_before_connecting(tmp_path):
    database_file = tmp_path / "nested" / "jakepilot.db"

    manager = SessionManager(f"sqlite:///{database_file.as_posix()}")
    try:
        assert database_file.is_file()
    finally:
        manager.close()


def test_database_router_uses_shared_database_configuration(monkeypatch, tmp_path):
    from db import db_router

    captured = {}
    database_file = tmp_path / "shared" / "jakepilot.db"

    class FakeSessionManager:
        def __init__(self, database_url):
            captured["database_url"] = database_url

        def close(self):
            pass

    monkeypatch.setattr(db_router, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(
        db_router.db_config,
        "db_path",
        f"sqlite:///{database_file.as_posix()}",
    )

    db_router.DatabaseRouter()

    assert captured["database_url"] == f"sqlite:///{database_file.as_posix()}"


def test_jakepilot_database_does_not_inherit_hermes_database_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/langchain_app",
    )
    monkeypatch.delenv("JAKEPILOT_DATABASE_URL", raising=False)

    config = DatabaseConfig()

    assert config.connection_string == "sqlite:///./data/jakepilot.db"
