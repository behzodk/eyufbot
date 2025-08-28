import os
from dotenv import load_dotenv
from supabase import create_client, Client
from zoneinfo import ZoneInfo

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
AWARD_CSV = os.getenv("AWARD_CSV", "award_holders.csv")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "Iltimos, har qanday muammolarni, jumladan texnik muammolarni, guruhga yozing: EYUF 2025 1-TANLOV")
UZ_TZ = ZoneInfo("Asia/Tashkent")

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN in .env")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)