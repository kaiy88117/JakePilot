from db.base.session_manager import SessionManager


def test_sqlite_parent_directory_is_created_before_connecting(tmp_path):
    database_file = tmp_path / "nested" / "jakepilot.db"

    manager = SessionManager(f"sqlite:///{database_file.as_posix()}")
    try:
        assert database_file.is_file()
    finally:
        manager.close()
