import re
import phonenumbers
from datetime import datetime, date, time, timedelta
from typing import Dict, List
from app.config import UZ_TZ

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def normalize_phone(raw: str):
    try:
        parsed = phonenumbers.parse(raw, "UZ")
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        return None

# Availability
WORK_WINDOWS = [(time(9,30), time(13,0)), (time(14,0), time(18,0))]
STEP_MIN = 5
CAPACITY = 2
MIN_AHEAD = timedelta(hours=2)

def ceil_dt_to_step(dt: datetime, step_min: int) -> datetime:
    m = (dt.minute // step_min) * step_min
    dt0 = dt.replace(second=0, microsecond=0, minute=m)
    if dt0 < dt:
        dt0 += timedelta(minutes=step_min)
    return dt0

def iter_window_candidates(day: date, win_start: time, win_end: time, dur: timedelta) -> List[datetime]:
    start_dt = datetime.combine(day, win_start, UZ_TZ)
    end_dt = datetime.combine(day, win_end, UZ_TZ)
    cur = start_dt
    out = []
    while True:
        if cur + dur <= end_dt:
            out.append(cur)
            cur += timedelta(minutes=STEP_MIN)
        else:
            break
    return out

def build_timeline(bookings: List[Dict], day: date) -> Dict[datetime, int]:
    counts: Dict[datetime, int] = {}
    for b in bookings:
        s = datetime.fromisoformat(b["start_at"]).astimezone(UZ_TZ)
        e = datetime.fromisoformat(b["end_at"]).astimezone(UZ_TZ)
        cur = ceil_dt_to_step(s.replace(second=0, microsecond=0), STEP_MIN)
        while cur < e:
            counts[cur] = counts.get(cur, 0) + 1
            cur += timedelta(minutes=STEP_MIN)
    return counts

def is_candidate_ok(start: datetime, dur: timedelta, counts: Dict[datetime,int]) -> bool:
    cur = start
    end = start + dur
    while cur < end:
        if counts.get(cur, 0) >= CAPACITY:
            return False
        cur += timedelta(minutes=STEP_MIN)
    return True

def list_available_times(day: date, duration_min: int, existing: List[Dict]) -> List[datetime]:
    now_local = datetime.now(UZ_TZ)
    dur = timedelta(minutes=int(duration_min))
    counts = build_timeline(existing, day)
    times: List[datetime] = []

    LUNCH_END = time(13, 0)
    LUNCH_EDGE_50 = time(12, 50)
    LUNCH_EDGE_55 = time(12, 55)

    for ws, we in WORK_WINDOWS:
        candidates = iter_window_candidates(day, ws, we, dur)
        for t0 in candidates:
            if t0 < now_local + MIN_AHEAD:
                continue

            if ws == WORK_WINDOWS[0][0] and we == LUNCH_END:
                if t0.time() == LUNCH_EDGE_55:
                    continue
                if t0.time() == LUNCH_EDGE_50 and duration_min > 10:
                    continue

            if t0 + dur > datetime.combine(day, we, UZ_TZ):
                continue
            if not is_candidate_ok(t0, dur, counts):
                continue
            times.append(t0)
    return times