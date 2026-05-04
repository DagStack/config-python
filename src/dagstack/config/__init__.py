"""dagstack.config — Python binding for dagstack/config-spec.

Public API:

    from dagstack.config import (
        Config,
        ConfigError,
        ConfigErrorReason,
        ConfigSource,
        Subscription,
        YamlFileSource,
        JsonFileSource,
        InMemorySource,
        # ADR-0002 Phase 2 — secrets
        SecretSource,
        AsyncSecretSource,
        SecretRef,
        SecretValue,
        ResolveContext,
        EnvSecretSource,
    )

Usage:

    config = Config.load("app-config.yaml")
    host = config.get_string("database.host")
    pool_size = config.get_int("database.pool_size", default=10)

    class DatabaseConfig(BaseModel):
        host: str
        password: str

    db = config.get_section("database", DatabaseConfig)

Phase 1 does not support runtime watch — `on_change` / `on_section_change`
register a subscription but the callback never fires (a warning is emitted
on the `dagstack.config.internal` logger). Explicit `config.reload()` is a
no-op.

Phase 2 (ADR-0002) adds `${secret:<scheme>:<path>}` interpolation with
pluggable `SecretSource` adapters. The mandatory `EnvSecretSource` is
auto-registered (so `${secret:env:VAR}` works without ceremony, and is
semantically identical to `${VAR}` from Phase 1). The pilot Vault
adapter ships in the `[vault]` extra: `pip install dagstack-config[vault]`.
"""

from dagstack.config._version import __version__
from dagstack.config.config import Config
from dagstack.config.errors import ConfigError, ConfigErrorReason
from dagstack.config.secrets import (
    AsyncSecretSource,
    EnvSecretSource,
    ResolveContext,
    SecretRef,
    SecretSource,
    SecretValue,
)
from dagstack.config.sources import (
    ConfigSource,
    InMemorySource,
    JsonFileSource,
    YamlFileSource,
)
from dagstack.config.subscription import Subscription

__all__ = [
    "AsyncSecretSource",
    "Config",
    "ConfigError",
    "ConfigErrorReason",
    "ConfigSource",
    "EnvSecretSource",
    "InMemorySource",
    "JsonFileSource",
    "ResolveContext",
    "SecretRef",
    "SecretSource",
    "SecretValue",
    "Subscription",
    "YamlFileSource",
    "__version__",
]
