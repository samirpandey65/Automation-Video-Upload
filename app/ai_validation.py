import re

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

def validate_metadata(title, description, hashtags, tags):
    errors = []
    title = str(title or '').strip()
    description = str(description or '').strip()
    if DEVANAGARI_RE.search(title + '\n' + description):
        errors.append('Devanagari detected; use English or Roman Hinglish only')
    if not 3 <= len(title) <= 100:
        errors.append('title must be 3-100 characters')
    if len(description) < 20:
        errors.append('description is too short')
    hs = [str(x).strip().lstrip('#').lower() for x in (hashtags or []) if str(x).strip()]
    ts = [str(x).strip().lstrip('#').lower() for x in (tags or []) if str(x).strip()]
    if not 2 <= len(hs) <= 6:
        errors.append('hashtags must contain 2-6 items')
    if not 5 <= len(ts) <= 15:
        errors.append('tags must contain 5-15 items')
    if any(len(x) > 40 for x in hs):
        errors.append('hashtag is too long')
    if any(x != x.lower() for x in hs + ts):
        errors.append('hashtags and tags must be lowercase')
    if re.search(r'^(this video is about|yeh video)', title, re.I):
        errors.append('title is too generic')
    return {'passed': not errors, 'errors': errors}
