import csv
import difflib
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

APOSTROPHE_VARIANTS = {"\u02bc", "\u02bb", "\u2018", "\u2019", "\u2032", "\uFF07"}

def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    for ch in APOSTROPHE_VARIANTS:
        s = s.replace(ch, "'")
    return re.sub(r"\s+", " ", s).strip().upper()

def load_award_map(path: str) -> Dict[str, str]:
    mp: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "name" not in (reader.fieldnames or []):
            raise SystemExit("award_holders.csv must include 'name' header.")
        for row in reader:
            raw = (row.get("name") or "").strip()
            if raw:
                mp[normalize_name(raw)] = raw
    if not mp:
        raise SystemExit("award_holders.csv contains no names.")
    return mp

def best_match_90(input_name: str, award_keys: List[str], award_map: Dict[str, str]) -> Optional[Tuple[str, str]]:
    q = normalize_name(input_name)
    cands = difflib.get_close_matches(q, award_keys, n=1, cutoff=0.90)
    if not cands:
        return None
    k = cands[0]
    return k, award_map[k]

def suggestion_names(input_name: str, award_keys: List[str], award_map: Dict[str, str], n: int = 5) -> List[str]:
    q = normalize_name(input_name)
    return [award_map[k] for k in difflib.get_close_matches(q, award_keys, n=n, cutoff=0.75)]