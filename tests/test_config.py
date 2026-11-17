"""Tests for configuration loading."""

import importlib

import pytest

REQUIRED_VARS = ["BETFAIR_USERNAME", "BETFAIR_PASSWORD", "BETFAIR_APP_KEY", "CERT_PATH"]


@pytest.fixture(autouse=True)
def restore_config_module():
    """Reload src.config after each test so reloads here don't leak elsewhere."""
    yield
    import src.config as config_module
    import src.settings as settings_module

    importlib.reload(settings_module)
    try:
        importlib.reload(config_module)
    except EnvironmentError:
        pass


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_config_fails_when_required_var_missing(monkeypatch, missing):
    """Importing config raises a clear error naming the missing variable."""
    import src.config as config_module

    # Stop load_dotenv() re-populating the variable from a local .env file
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for name in REQUIRED_VARS:
        monkeypatch.setenv(name, "value")
    monkeypatch.delenv(missing)

    with pytest.raises(EnvironmentError, match=missing):
        importlib.reload(config_module)


def test_optional_logging_vars_have_defaults(monkeypatch):
    """LOG_LEVEL and LOG_DIR fall back to defaults when unset."""
    import src.config as config_module

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for name in REQUIRED_VARS:
        monkeypatch.setenv(name, "value")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STREAM_DIR", raising=False)

    import src.settings as settings_module

    importlib.reload(settings_module)
    importlib.reload(config_module)

    assert config_module.Config.DATABASE_URL.startswith("postgresql://")
    assert config_module.Config.STREAM_DIR == "data/streams"
    assert config_module.Config.LOG_LEVEL == "INFO"
    assert config_module.Config.LOG_DIR == "logs"


def test_config_loads_from_env(monkeypatch, tmp_path):
    """Config reads required values from environment variables."""
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()

    monkeypatch.setenv("BETFAIR_USERNAME", "alice@test.com")
    monkeypatch.setenv("BETFAIR_PASSWORD", "hunter2")
    monkeypatch.setenv("BETFAIR_APP_KEY", "myAppKey")
    monkeypatch.setenv("CERT_PATH", str(cert_dir))

    # Re-import to pick up monkeypatched env vars
    import src.config as config_module
    importlib.reload(config_module)
    Config = config_module.Config

    assert Config.BETFAIR_USERNAME == "alice@test.com"
    assert Config.BETFAIR_PASSWORD == "hunter2"
    assert Config.BETFAIR_APP_KEY == "myAppKey"
    assert Config.CERT_PATH == str(cert_dir)


def test_cert_paths_are_constructed_correctly(monkeypatch, tmp_path):
    """Config.cert_crt() and cert_key() return correct file paths."""
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()

    monkeypatch.setenv("BETFAIR_USERNAME", "user")
    monkeypatch.setenv("BETFAIR_PASSWORD", "pass")
    monkeypatch.setenv("BETFAIR_APP_KEY", "key")
    monkeypatch.setenv("CERT_PATH", str(cert_dir))

    import src.config as config_module
    importlib.reload(config_module)
    Config = config_module.Config

    assert Config.cert_crt().endswith("client-2048.crt")
    assert Config.cert_key().endswith("client-2048.key")
    assert str(cert_dir) in Config.cert_crt()


def test_settings_need_no_credentials(monkeypatch):
    """src.settings imports and reads overrides with no Betfair variables set."""
    import src.settings as settings_module

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.setenv("STREAM_DIR", "/recordings")

    importlib.reload(settings_module)

    assert settings_module.Settings.DATABASE_URL == "postgresql://example/db"
    assert settings_module.Settings.STREAM_DIR == "/recordings"
