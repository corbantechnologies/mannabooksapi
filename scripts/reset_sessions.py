"""
Sign every user out of every device (use before staging/live testing or after a security incident).

What it does, in one transaction:
  * users.tokens_valid_after = now()   -> every access token issued so far is rejected
  * revokes all refresh tokens         -> no silent re-login
  * deletes legacy DB sessions and pending password-reset links
  * clears the JWT denylist (no longer needed once every older token is invalid)

Usage (runs against DATABASE_URL from the environment / .env):

    python scripts/reset_sessions.py --yes

Browsers still holding old cookies are simply redirected to the login page.
"""
import asyncio
import sys

from sqlalchemy import text

from app.db.session import engine

STATEMENTS = [
    "UPDATE users SET tokens_valid_after = (now() AT TIME ZONE 'utc')",
    "UPDATE refresh_tokens SET revoked_at = (now() AT TIME ZONE 'utc'), revoke_reason = 'reset' WHERE revoked_at IS NULL",
    "DELETE FROM sessions",
    "DELETE FROM password_reset_tokens",
    "DELETE FROM revoked_tokens",
]


async def main() -> None:
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            result = await conn.execute(text(stmt))
            print(f"{result.rowcount:>6} rows  {stmt.split(' WHERE')[0]}")
    await engine.dispose()
    print("All sessions reset. Every user must sign in again.")


if __name__ == "__main__":
    if "--yes" not in sys.argv:
        print(__doc__)
        print("Refusing to run without --yes.")
        sys.exit(1)
    asyncio.run(main())
