"""Thin Supabase wrapper. Credentials come from environment variables
set as GitHub Actions secrets — never hard-code them.

    SUPABASE_URL          = https://<project>.supabase.co
    SUPABASE_SERVICE_KEY  = service-role key (write access; keep secret)
"""
import os
from supabase import create_client, Client

_client: Client | None = None


def db() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


def upsert(table: str, rows: list[dict], on_conflict: str | None = None):
    if not rows:
        return
    if on_conflict:
        return db().table(table).upsert(rows, on_conflict=on_conflict).execute()
    return db().table(table).upsert(rows).execute()


def fetch(table: str, **eq) -> list[dict]:
    q = db().table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data


def update(table: str, values: dict, **eq):
    """Partial update of existing rows — use instead of upsert when not all
    not-null columns are present (e.g. updating only a stop level by id)."""
    q = db().table(table).update(values)
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute()


def insert(table: str, rows: list[dict]):
    if not rows:
        return
    return db().table(table).insert(rows).execute()


def instrument_id_map() -> dict[str, int]:
    """ticker -> instruments.id"""
    return {r["ticker"]: r["id"] for r in db().table("instruments").select("id,ticker").execute().data}
