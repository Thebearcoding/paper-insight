import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import database


class FakeCursor:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.current_one = None
        self.current_all = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        sql = str(query)
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        self.current_one = None
        self.current_all = []

        if "SELECT 1 FROM llm_providers" in normalized:
            return
        if "INSERT INTO llm_providers" in normalized:
            self.current_one = {
                "id": uuid.uuid4(),
                "provider_key": "agentrouter",
                "name": "AgentRouter",
                "base_url": "https://agentrouter.org/v1",
                "api_key": "test-api-key",
                "is_active": False,
                "is_enabled": True,
                "is_builtin": False,
                "active_model": "claude-opus-5",
                "default_parameters": {},
                "models_fetched_at": None,
                "created_at": None,
                "updated_at": None,
            }
            return
        if "SELECT id, provider_id, model_name" in normalized:
            self.current_all = []

    def fetchone(self):
        return self.current_one

    def fetchall(self):
        return self.current_all


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


def _configure_encryption_key(monkeypatch, value="server-encryption-key"):
    monkeypatch.setattr(
        database,
        "settings",
        SimpleNamespace(llm=SimpleNamespace(credential_encryption_key=value)),
    )


def test_llm_encryption_key_is_required(monkeypatch):
    _configure_encryption_key(monkeypatch, "")

    with pytest.raises(database.DatabaseError, match="credential_encryption_key"):
        database._llm_encryption_key()


def test_legacy_llm_keys_are_encrypted_and_cleared():
    cursor = FakeCursor()

    database._migrate_legacy_llm_api_keys(cursor, "server-encryption-key")

    query, params = cursor.calls[-1]
    assert "pgp_sym_encrypt" in query
    assert "cipher-algo=aes256" in query
    assert "api_key = NULL" in query
    assert params == ("server-encryption-key",)


def test_create_llm_provider_encrypts_api_key(monkeypatch):
    _configure_encryption_key(monkeypatch)
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)

    provider = database.create_llm_provider(
        "AgentRouter",
        "https://agentrouter.org/v1",
        "test-api-key",
        ["claude-opus-5"],
    )

    insert_query, insert_params = next(
        call for call in cursor.calls if "INSERT INTO llm_providers" in call[0]
    )
    assert "encrypted_api_key" in insert_query
    assert "pgp_sym_encrypt" in insert_query
    assert "cipher-algo=aes256" in insert_query
    assert "NULL, FALSE" in insert_query
    assert insert_params.count("test-api-key") == 2
    assert insert_params.count("server-encryption-key") == 2
    assert provider["provider_key"] == "agentrouter"
    assert connection.committed is True


def test_list_llm_providers_decrypts_only_for_runtime_use(monkeypatch):
    _configure_encryption_key(monkeypatch)
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)

    assert database.list_llm_providers(include_models=False) == []

    query, params = next(
        call for call in cursor.calls if "FROM llm_providers" in call[0]
    )
    assert "pgp_sym_decrypt" in query
    assert params == ("server-encryption-key",)
