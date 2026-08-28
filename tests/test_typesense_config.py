import importlib.util
import stat
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import config


def _load_docker_compose_module():
    module_path = REPO_ROOT / "scripts" / "docker_compose.py"
    spec = importlib.util.spec_from_file_location("paper_insight_docker_compose", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_typesense_config_supports_environment_overrides(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
database:
  url: postgresql:///paper_online
typesense:
  enabled: false
  host: yaml-host
  api_key: yaml-key
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setenv("TYPESENSE_ENABLED", "true")
    monkeypatch.setenv("TYPESENSE_HOST", "typesense")
    monkeypatch.setenv("TYPESENSE_API_KEY", "env-key")
    monkeypatch.setenv("TYPESENSE_VECTOR_ALPHA", "0.6")

    loaded = config.load_app_config()

    assert loaded.typesense.enabled is True
    assert loaded.typesense.host == "typesense"
    assert loaded.typesense.api_key == "env-key"
    assert loaded.typesense.vector_alpha == 0.6


def test_openalex_config_supports_optional_key_and_environment_overrides(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
database:
  url: postgresql:///paper_online
openalex:
  api_key: yaml-key
  base_url: https://yaml.openalex.example/
  timeout_seconds: 12
  cache_ttl_seconds: 1200
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENALEX_API_KEY", "env-key")
    monkeypatch.setenv("OPENALEX_BASE_URL", "https://api.openalex.org/")
    monkeypatch.setenv("OPENALEX_TIMEOUT_SECONDS", "21")
    monkeypatch.setenv("OPENALEX_CACHE_TTL_SECONDS", "900")

    loaded = config.load_app_config()

    assert loaded.openalex.api_key == "env-key"
    assert loaded.openalex.base_url == "https://api.openalex.org"
    assert loaded.openalex.timeout_seconds == 21
    assert loaded.openalex.cache_ttl_seconds == 900


def test_compose_helper_generates_and_reuses_typesense_key(tmp_path, monkeypatch):
    docker_compose = _load_docker_compose_module()
    generated_dir = tmp_path / ".docker"
    generated_env_path = generated_dir / "compose.env"
    monkeypatch.setattr(docker_compose, "generated_dir", generated_dir)
    monkeypatch.setattr(docker_compose, "generated_env_path", generated_env_path)
    payload = {
        "database": {
            "url": "postgresql://paper:password@postgres:5432/paper_online",
        },
        "server": {"port": 8000},
        "docker": {"postgres_port": 5432, "typesense_port": 8108},
    }

    docker_compose.write_compose_env(payload)
    first = generated_env_path.read_text(encoding="utf-8")
    docker_compose.write_compose_env(payload)
    second = generated_env_path.read_text(encoding="utf-8")

    first_key = next(line for line in first.splitlines() if line.startswith("TYPESENSE_API_KEY="))
    second_key = next(line for line in second.splitlines() if line.startswith("TYPESENSE_API_KEY="))
    assert first_key == second_key
    assert len(first_key.split("=", 1)[1]) == 64
    assert "TYPESENSE_HTTP_PORT=8108" in second
    assert stat.S_IMODE(generated_env_path.stat().st_mode) == 0o600
