from datetime import datetime
from typing import Dict, List, Optional

from app.config import sb

# --- Users ---
def is_registered_sync(telegram_user_id: int) -> bool:
    res = sb.table("app_user").select("id").eq("telegram_user_id", telegram_user_id).limit(1).execute()
    return bool(res.data)

def is_name_taken_sync(canonical_full_name: str) -> bool:
    res = sb.table("app_user").select("id").ilike("full_name", canonical_full_name).limit(1).execute()
    return bool(res.data)

def register_user_sync(telegram_user_id: int, full_name: str, phone: str, email: str, country: str, university: str) -> Dict:
    res = sb.table("app_user").insert({
        "telegram_user_id": telegram_user_id,
        "full_name": full_name.strip(),
        "phone": phone,
        "email": email.lower(),
        "country": country.strip(),
        "university": university.strip(),
    }).execute()
    if not res.data:
        raise RuntimeError("Insert returned no data")
    return res.data[0]

def get_user_record_sync(telegram_user_id: int) -> Optional[Dict]:
    res = sb.table("app_user").select("*").eq("telegram_user_id", telegram_user_id).single().execute()
    return res.data if res else None

# --- Services ---
def fetch_services_sync() -> List[Dict]:
    return (sb.table("service").select("id,name,duration_min").order("name").execute().data) or []

def get_service_sync(svc_id: str) -> Optional[Dict]:
    try:
        return sb.table("service").select("id,name,duration_min").eq("id", svc_id).single().execute().data
    except Exception:
        return None

# --- Bookings ---
def fetch_bookings_for_day_sync(day_start: datetime, day_end: datetime) -> List[Dict]:
    return (sb.table("booking")
              .select("id,user_id,service_id,start_at,end_at")
              .lt("start_at", day_end.isoformat())
              .gt("end_at", day_start.isoformat())
              .execute()
              .data) or []

def create_booking_sync(user_id: str, service_id: str, start_at: datetime, end_at: datetime) -> Dict:
    res = sb.table("booking").insert({
        "user_id": user_id,
        "service_id": service_id,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "status": "booked",
    }).execute()
    if not res.data:
        raise RuntimeError("Booking insert returned no data")
    return res.data[0]