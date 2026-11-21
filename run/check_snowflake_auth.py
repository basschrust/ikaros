"""Test Snowflake authentication independently of the active Ikaros backend.

Use --totp to enter a one-time six-digit code without persisting it to .env,
PowerShell history, command-line arguments, or source files.
"""

from __future__ import annotations

import argparse
from getpass import getpass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--totp", action="store_true", help="Prompt securely for current MFA code")
    args = parser.parse_args(argv)
    from config.config import load_config
    from database.authenticated_snowflake import connection_arguments
    import snowflake.connector

    config = load_config("database.yaml")["database"].get("snowflake", {})
    params = connection_arguments(**config)
    if args.totp:
        code = getpass("Snowflake TOTP code (6 digits): ").strip()
        if not (len(code) == 6 and code.isascii() and code.isdigit()):
            parser.error("TOTP must contain exactly six digits")
        params["passcode"] = code
    with snowflake.connector.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_SCHEMA()"
            )
            account, user, role, schema = cur.fetchone()
            print(
                f"Snowflake authentication OK: account={account} user={user} role={role} schema={schema}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
