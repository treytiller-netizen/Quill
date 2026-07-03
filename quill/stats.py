"""Aggregate stats + locally-computed speaking characteristics for the Hub."""

import datetime
import difflib
import json
import re
import time
from collections import Counter

from . import config, history

VOICE_FILE = config.CONFIG_DIR / "voice.json"
VOICE_UPDATE_EVERY_WORDS = 1000

_STOPWORDS = set(
    "the a an and or but so of to in on for with at by from up about into is are was "
    "were be been being have has had do does did will would can could should i you he "
    "she it we they me him her us them my your his its our their this that these those "
    "not no yes if then than as just also really very it's i'm don't that's".split()
)


def dictionary_terms() -> list[dict]:
    """[{term, replacement|None}] from the dictionary file."""
    try:
        lines = config.DICTIONARY_FILE.read_text().splitlines()
    except FileNotFoundError:
        return []
    terms = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            left, right = (s.strip() for s in line.split("->", 1))
            terms.append(dict(term=left, replacement=right))
        else:
            terms.append(dict(term=line, replacement=None))
    return terms


def dictionary_hits() -> Counter:
    """How often each dictionary term (or its replacement) appears in output."""
    terms = dictionary_terms()
    hits: Counter = Counter()
    texts = [e["text"] for e in history.entries(limit=1000)]
    blob = "\n".join(texts).lower()
    for t in terms:
        needle = (t["replacement"] or t["term"]).lower()
        if needle:
            hits[t["term"]] = blob.count(needle)
    return hits


def corrections() -> dict:
    """Words Quill changed between the raw transcript and the final text."""
    changed = 0
    for raw, clean in history.raw_clean_pairs():
        raw_words, clean_words = raw.lower().split(), clean.lower().split()
        matcher = difflib.SequenceMatcher(a=raw_words, b=clean_words, autojunk=False)
        for op, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if op in ("replace", "delete"):
                changed += i2 - i1
    dict_fixes = sum(dictionary_hits().values())
    return dict(words_corrected=changed, dictionary_fixes=dict_fixes,
                total=changed + dict_fixes)


def most_used_words(n: int = 5) -> list[tuple[str, int]]:
    words = re.findall(r"[a-z']+", history.all_text().lower())
    counts = Counter(w for w in words if w not in _STOPWORDS and len(w) > 2)
    return counts.most_common(n)


def peak_hour() -> str | None:
    hours = Counter(
        datetime.datetime.fromtimestamp(e["ts"]).strftime("%A at %-I %p").replace(" 0", " ")
        for e in history.entries(limit=1000)
    )
    if not hours:
        return None
    (label, count), = hours.most_common(1)
    return label if count >= 2 else None


def streak() -> dict:
    by_day = history.words_by_day(days=400)
    today = datetime.date.today()
    current = 0
    day = today
    # today counts if you've dictated; otherwise streak is measured through yesterday
    if by_day.get(day.isoformat(), 0) == 0:
        day -= datetime.timedelta(days=1)
    while by_day.get(day.isoformat(), 0) > 0:
        current += 1
        day -= datetime.timedelta(days=1)
    longest, run = 0, 0
    for i in range(400, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        run = run + 1 if by_day.get(d, 0) > 0 else 0
        longest = max(longest, run)
    return dict(current=current, longest=longest)


def voice_profile() -> dict:
    """Cached Claude-generated voice profile + progress toward the next update."""
    total_words = history.totals()["words"]
    profile = {}
    try:
        profile = json.loads(VOICE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    generated_at_words = profile.get("generated_at_words", 0)
    words_until_update = max(
        0, VOICE_UPDATE_EVERY_WORDS - (total_words - generated_at_words)
    )
    return dict(profile=profile.get("profile"), words_until_update=words_until_update,
                progress=1 - words_until_update / VOICE_UPDATE_EVERY_WORDS)


def save_voice_profile(profile: dict) -> None:
    config.CONFIG_DIR.mkdir(exist_ok=True)
    VOICE_FILE.write_text(json.dumps(dict(
        profile=profile,
        generated_at_words=history.totals()["words"],
        generated_at=time.time(),
    )))


def voice_profile_due() -> bool:
    return voice_profile()["words_until_update"] == 0 and history.totals()["words"] > 50


def hub_payload() -> dict:
    """Everything the Hub UI needs, JSON-serializable."""
    t = history.totals()
    essays = t["words"] / 12_500  # ~a college essay
    fixes = corrections()
    top_words = most_used_words()
    return dict(
        user="Trey",
        totals=t,
        essays=round(essays, 1),
        minutes_spoken=round(t["spoken_seconds"] / 60),
        fixes=fixes,
        streak=streak(),
        by_day=history.words_by_day(),
        apps=[list(x) for x in history.app_usage()],
        entries=history.entries(limit=300),
        dictionary=dictionary_terms(),
        dict_hits=dict(dictionary_hits()),
        most_used=top_words,
        peak=peak_hour(),
        voice=voice_profile(),
        today=datetime.date.today().isoformat(),
    )
