"""Application configuration and filesystem paths.

Configuration resolution order (last wins):

1. built-in defaults
2. the JSON configuration file in the application data directory
3. ``CLOCKMANAGER_*`` environment variables

No device address, credential or secret is stored in source.

Two settings gate device writing. They default to off and must be turned on
deliberately, because no NG-MB1 has yet accepted a record from this
application's write path (``PROTOCOL.md``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from clockmanager.errors import ConfigurationError

__all__ = [
    "CONFIG_FILE_NAME",
    "DATABASE_FILE_NAME",
    "ENV_PREFIX",
    "AppConfig",
    "AppPaths",
    "default_data_dir",
    "load_config",
    "save_config",
]

ENV_PREFIX: Final = "CLOCKMANAGER_"
CONFIG_FILE_NAME: Final = "config.json"
DATABASE_FILE_NAME: Final = "clockmanager.sqlite3"
_APP_DIR_NAME: Final = "NGTecoClockManager"

_VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
)
_VALID_LOG_FORMATS: Final[frozenset[str]] = frozenset({"json", "text"})


def default_data_dir() -> Path:
    """Return the per-user application data directory for this platform."""
    override = os.environ.get(f"{ENV_PREFIX}DATA_DIR")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / _APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / _APP_DIR_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / _APP_DIR_NAME
    return Path.home() / ".local" / "share" / _APP_DIR_NAME


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved filesystem locations used by the application."""

    data_dir: Path

    @property
    def config_file(self) -> Path:
        return self.data_dir / CONFIG_FILE_NAME

    @property
    def database_file(self) -> Path:
        return self.data_dir / DATABASE_FILE_NAME

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure(self) -> None:
        """Create the directories the application writes to."""
        for directory in (self.data_dir, self.log_dir, self.backup_dir):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Immutable application configuration."""

    paths: AppPaths = field(default_factory=lambda: AppPaths(default_data_dir()))
    log_level: str = "INFO"
    log_format: str = "json"
    log_to_console: bool = True
    #: Gates raw protocol/diagnostic views. Restricted to administrators
    #: (SECURITY.md); never enables unredacted credential output.
    developer_mode: bool = False
    #: SQLAlchemy echo. Off by default because it is noisy, not because it is
    #: unsafe: statements are parameterised.
    database_echo: bool = False
    #: Run against the built-in mock device instead of real hardware, so the
    #: application can be developed and demonstrated with no clock attached.
    use_mock_device: bool = False
    #: Unlock user create/update/delete on the device. OFF by default: the
    #: 120-byte write path is unit-tested but no MB1 has accepted a record from
    #: it, so it stays locked until an administrator enables it and proves it
    #: with a disposable test user (AGENTS.md, PROTOCOL.md).
    enable_device_writes: bool = False
    #: Unlock writing the credential region (setting or clearing a PIN). OFF by
    #: default and ineffective unless ``enable_device_writes`` is also on: the
    #: region's internal layout is unverified.
    enable_credential_writes: bool = False

    def __post_init__(self) -> None:
        if self.log_level.upper() not in _VALID_LOG_LEVELS:
            raise ConfigurationError(
                f"Invalid log_level {self.log_level!r}; expected one of {sorted(_VALID_LOG_LEVELS)}"
            )
        if self.log_format.lower() not in _VALID_LOG_FORMATS:
            raise ConfigurationError(
                f"Invalid log_format {self.log_format!r}; expected one of "
                f"{sorted(_VALID_LOG_FORMATS)}"
            )
        object.__setattr__(self, "log_level", self.log_level.upper())
        object.__setattr__(self, "log_format", self.log_format.lower())

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for the local SQLite database."""
        return f"sqlite+pysqlite:///{self.paths.database_file.as_posix()}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise the file-backed settings (paths are resolved separately)."""
        return {
            "log_level": self.log_level,
            "log_format": self.log_format,
            "log_to_console": self.log_to_console,
            "developer_mode": self.developer_mode,
            "database_echo": self.database_echo,
            "use_mock_device": self.use_mock_device,
            "enable_device_writes": self.enable_device_writes,
            "enable_credential_writes": self.enable_credential_writes,
        }


def _coerce_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ConfigurationError(f"Invalid boolean value for {name}: {value!r}")


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Could not read configuration file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Configuration file {path} must contain a JSON object")
    return raw


def _environment_overrides(environ: dict[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    mapping = {
        f"{ENV_PREFIX}LOG_LEVEL": "log_level",
        f"{ENV_PREFIX}LOG_FORMAT": "log_format",
        f"{ENV_PREFIX}LOG_TO_CONSOLE": "log_to_console",
        f"{ENV_PREFIX}DEVELOPER_MODE": "developer_mode",
        f"{ENV_PREFIX}DATABASE_ECHO": "database_echo",
        f"{ENV_PREFIX}USE_MOCK_DEVICE": "use_mock_device",
        f"{ENV_PREFIX}ENABLE_DEVICE_WRITES": "enable_device_writes",
        f"{ENV_PREFIX}ENABLE_CREDENTIAL_WRITES": "enable_credential_writes",
    }
    for env_name, field_name in mapping.items():
        if env_name in environ:
            overrides[field_name] = environ[env_name]
    return overrides


def load_config(
    *,
    data_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> AppConfig:
    """Load configuration from defaults, the config file and the environment."""
    env = dict(os.environ if environ is None else environ)

    if data_dir is not None:
        resolved_dir = Path(data_dir).expanduser()
    elif f"{ENV_PREFIX}DATA_DIR" in env:
        resolved_dir = Path(env[f"{ENV_PREFIX}DATA_DIR"]).expanduser()
    else:
        resolved_dir = default_data_dir()

    paths = AppPaths(resolved_dir)
    settings: dict[str, Any] = {}
    settings.update(_read_config_file(paths.config_file))
    settings.update(_environment_overrides(env))

    known = set(AppConfig().to_dict())
    unknown = sorted(set(settings) - known)
    if unknown:
        raise ConfigurationError(f"Unknown configuration keys: {', '.join(unknown)}")

    for bool_field in (
        "log_to_console",
        "developer_mode",
        "database_echo",
        "use_mock_device",
        "enable_device_writes",
        "enable_credential_writes",
    ):
        if bool_field in settings:
            settings[bool_field] = _coerce_bool(settings[bool_field], name=bool_field)

    for text_field in ("log_level", "log_format"):
        if text_field in settings and not isinstance(settings[text_field], str):
            raise ConfigurationError(f"Configuration key {text_field} must be a string")

    return replace(AppConfig(paths=paths), **settings)


def save_config(config: AppConfig) -> Path:
    """Write the file-backed settings and return the configuration file path."""
    config.paths.ensure()
    target = config.paths.config_file
    try:
        target.write_text(
            json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ConfigurationError(f"Could not write configuration file {target}: {exc}") from exc
    return target
