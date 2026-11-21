"""Snowflake connection modes: MFA/TOTP for people, key-pair/PAT for jobs."""

from __future__ import annotations

import os
from typing import Any

from database.snowflake_storage import SnowflakeRepository, _ident


def connection_arguments(
    *,
    database: str = "DAEDALUS",
    schema: str = "MARKET_DATA",
    warehouse: str = "DAEDALUS_WH",
    account: str | None = None,
    user: str | None = None,
    password: str | None = None,
    authenticator: str = "snowflake",
    role: str | None = None,
    private_key_file: str | None = None,
    private_key_password: str | None = None,
    passcode: str | None = None,
) -> dict[str, Any]:
    """Build connector kwargs without reading secrets from config files."""
    mode = os.getenv("SNOWFLAKE_AUTHENTICATOR", authenticator).strip().lower()
    if mode == "keypair":
        mode = "snowflake_jwt"
    kwargs: dict[str, Any] = {
        "account": account or os.environ["SNOWFLAKE_ACCOUNT"],
        "user": user or os.environ["SNOWFLAKE_USER"],
        "database": _ident(database),
        "warehouse": _ident(warehouse),
        "authenticator": mode,
    }
    if role or os.getenv("SNOWFLAKE_ROLE"):
        kwargs["role"] = _ident(role or os.environ["SNOWFLAKE_ROLE"])
    if mode == "snowflake_jwt":
        key_file = private_key_file or os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE")
        if not key_file:
            raise ValueError("Key-pair authentication requires SNOWFLAKE_PRIVATE_KEY_FILE")
        kwargs["private_key_file"] = key_file
        # Connector 4.x supports private_key_file_pwd; 5.x names this
        # private_key_password. Use password keyword from the installed version.
        key_pwd = private_key_password or os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSWORD")
        if key_pwd:
            kwargs["private_key_file_pwd"] = key_pwd
    elif mode == "externalbrowser":
        pass
    elif mode in ("snowflake", "username_password_mfa"):
        secret = (
            password
            or os.getenv("SNOWFLAKE_PROGRAMMATIC_ACCESS_TOKEN")
            or os.getenv("SNOWFLAKE_PASSWORD")
        )
        if not secret:
            raise ValueError("Snowflake password or PAT missing")
        kwargs["password"] = secret
        mfa = passcode or os.getenv("SNOWFLAKE_MFA_PASSCODE")
        if mfa:
            if not (mfa.isascii() and len(mfa) == 6 and mfa.isdigit()):
                raise ValueError("SNOWFLAKE_MFA_PASSCODE must be six digits")
            kwargs["passcode"] = mfa
        if mode == "username_password_mfa":
            kwargs["client_request_mfa_token"] = True
            kwargs["client_store_temporary_credential"] = True
    else:
        raise ValueError(f"Unsupported Snowflake authenticator {mode!r}")
    return kwargs


class AuthenticatedSnowflakeRepository(SnowflakeRepository):
    """Connect using the selected authentication method, then initialize schema."""

    def __init__(self, **params: Any) -> None:
        import snowflake.connector

        self.database = _ident(str(params.get("database", "DAEDALUS")))
        self.schema = _ident(str(params.get("schema", "MARKET_DATA")))
        self.warehouse = _ident(str(params.get("warehouse", "DAEDALUS_WH")))
        self.connection = snowflake.connector.connect(**connection_arguments(**params))
        try:
            self._execute(f'CREATE SCHEMA IF NOT EXISTS "{self.database}"."{self.schema}"')
            self._execute(f'USE SCHEMA "{self.database}"."{self.schema}"')
            self.prepare_tables()
        except Exception:
            self.close()
            raise
