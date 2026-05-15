import os
import sys
import traceback
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR.parent / ".env", SCRIPT_DIR.parent.parent / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Missing SUPABASE_URL or SUPABASE_KEY in .env")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

today = date.today().isoformat()
row = {
    "date": today,
    "verdict": "TEST",
    "strike_count": 0,
    "verdict_reason": "connection test",
}

try:
    insert_resp = supabase.table("daily_verdicts").insert(row).execute()
    inserted = insert_resp.data[0] if insert_resp.data else None
    print("Supabase connection verified")

    if inserted and inserted.get("id"):
        supabase.table("daily_verdicts").delete().eq("id", inserted["id"]).execute()
    else:
        supabase.table("daily_verdicts").delete().eq("date", today).eq("verdict", "TEST").execute()
except Exception as e:
    print("Supabase insert failed:")
    print(f"{type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)
