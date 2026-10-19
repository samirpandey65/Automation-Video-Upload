import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from .youtube import list_playlists, create_playlist, add_to_playlist, update_playlist

log = logging.getLogger(__name__)

# Comprehensive genre taxonomy with multi-word keywords, clean titles, and folder path hints
GENRE_TAXONOMY: List[Dict[str, Any]] = [
    {
        "genre": "Football & Sports",
        "clean_title": "Football & Sports",
        "keywords": [
            "football", "soccer", "fifa", "fifa world cup", "messi", "ronaldo", "neymar",
            "mbappe", "haaland", "goal", "goals", "penalty", "dribble", "freekick",
            "premier league", "champions league", "world cup", "cricket", "ipl", "match",
            "highlights", "skills", "sports", "athlete", "stadium", "tournament", "ballon d'or",
            "assist", "tackle", "man city", "real madrid", "barcelona", "striker", "goalkeeper"
        ],
        "path_hints": ["fifa", "football", "soccer", "sports", "cricket", "world cup"]
    },
    {
        "genre": "Food & Recipes",
        "clean_title": "Food & Recipes",
        "keywords": [
            "food", "desi food", "street food", "recipe", "recipes", "cooking", "kitchen",
            "chef", "dish", "dishes", "taste", "eating", "snack", "snacks", "curry", "sweet",
            "biryani", "chai", "zaika", "dhaba", "restaurant", "swad", "masala", "foodie",
            "mukbang", "streetfood", "delicious", "tasty", "indian food", "chole", "samosa",
            "panipuri", "chaat", "mithai", "lunch", "dinner", "breakfast", "thali"
        ],
        "path_hints": ["food", "recipe", "cooking", "desi food", "viral-desi-food", "deehastore"]
    },
    {
        "genre": "Animated Stories & Kahaniya",
        "clean_title": "Animated Stories & Kahaniya",
        "keywords": [
            "kahaniya", "kahani", "chalaak", "chalaak kahaniya", "cartoon", "cartoons",
            "animation", "animated", "bedtime story", "moral story", "moral stories",
            "fairy tale", "panchatantra", "dadi ma", "bachpan", "toon", "toons", "kids story",
            "hindi kahani", "stories", "tale", "jadui", "chamatkari", "raja rani", "akbar birbal",
            "motu patlu", "bhoot ki kahani", "seekh"
        ],
        "path_hints": ["chalaakkahaniya", "kahaniya", "kahani", "toon", "animation", "cartoon", "bachpan"]
    },
    {
        "genre": "Gaming",
        "clean_title": "Gaming & Walkthroughs",
        "keywords": [
            "gaming", "gameplay", "gamer", "pc gaming", "gta", "gta v", "gta 5", "gta 6",
            "minecraft", "bgmi", "pubg", "free fire", "playthrough", "walkthrough", "game",
            "games", "ps5", "xbox", "pc games", "mod", "speedrun", "streamer", "esports",
            "multiplayer", "roblox", "call of duty", "cod"
        ],
        "path_hints": ["gaming", "pc-gaming", "game", "gta", "minecraft", "bgmi"]
    },
    {
        "genre": "Comedy & Humor",
        "clean_title": "Comedy & Fun",
        "keywords": [
            "comedy", "funny", "laugh", "laughter", "joke", "jokes", "prank", "pranks",
            "hilarious", "hasna", "hasobhai", "standup", "chutkule", "memes", "meme",
            "dank", "fun", "comic", "parody", "lol", "humor", "haso", "comedy video"
        ],
        "path_hints": ["hasobhai", "comedy", "funny", "memes", "prank", "haso"]
    },
    {
        "genre": "Horror & Supernatural",
        "clean_title": "Horror & Paranormal Stories",
        "keywords": [
            "horror", "scary", "dark", "ghost", "ghosts", "haunted", "bhoot", "dar", "darr",
            "creepy", "witch", "nightmare", "chudail", "aatma", "paranormal", "demon",
            "spooky", "haunting", "possessed", "evil", "shaitan"
        ],
        "path_hints": ["horror", "bhoot", "scary", "ghost", "chudail"]
    },
    {
        "genre": "Movie & Story Recaps",
        "clean_title": "Movie & Story Recaps",
        "keywords": [
            "movie recap", "film recap", "cinema recap", "ending explained", "story recap",
            "movie explained", "film explained", "plot summary", "recap", "explained in hindi",
            "cinema", "hollywood recap", "bollywood recap", "web series explained"
        ],
        "path_hints": ["recap", "movie", "film"]
    },
    {
        "genre": "Tech & Tutorials",
        "clean_title": "Tech & Tutorials",
        "keywords": [
            "technology", "tech", "coding", "programming", "software", "gadget", "gadgets",
            "smartphone", "iphone", "android", "computer", "pc build", "ai", "artificial intelligence",
            "unboxing", "review", "tutorial", "how to", "science", "innovation", "tips and tricks"
        ],
        "path_hints": ["tech", "coding", "tutorial", "software", "gadget"]
    },
    {
        "genre": "Motivation & Inspiration",
        "clean_title": "Motivation & Inspiration",
        "keywords": [
            "motivation", "motivational", "inspire", "inspirational", "success", "mindset",
            "hustle", "quote", "quotes", "discipline", "advice", "life lesson", "wisdom", "gyan"
        ],
        "path_hints": ["motivation", "inspiration", "mindset"]
    },
    {
        "genre": "Music & Audio",
        "clean_title": "Music & Songs",
        "keywords": [
            "music", "song", "songs", "gana", "rap", "beat", "melody", "lyrics", "lofi",
            "singer", "track", "remix", "acoustic", "instrumental", "dance beat"
        ],
        "path_hints": ["music", "songs", "lofi", "rap"]
    },
    {
        "genre": "Crime & Mystery",
        "clean_title": "Crime & Mystery",
        "keywords": [
            "crime", "investigation", "police", "jail", "cops", "cid", "murder", "scam",
            "case", "detective", "mystery", "true crime", "suspect", "court", "arrested", "kand"
        ],
        "path_hints": ["crime", "police", "cid", "mystery"]
    },
    {
        "genre": "Roast & Reactions",
        "clean_title": "Roast & Reactions",
        "keywords": [
            "roast", "roasting", "reaction", "react", "reacting", "response", "troll",
            "exposed", "savage", "roaster", "dank indian memes"
        ],
        "path_hints": ["roast", "reaction", "react"]
    },
    {
        "genre": "Travel & Lifestyle",
        "clean_title": "Travel & Vlogs",
        "keywords": [
            "travel", "vlog", "vlogs", "trip", "adventure", "tour", "explore", "destination",
            "journey", "lifestyle", "daily vlog", "city", "hotel", "mountains", "beach"
        ],
        "path_hints": ["travel", "vlog", "tour"]
    },
    {
        "genre": "News & Updates",
        "clean_title": "News & Updates",
        "keywords": [
            "news", "breaking", "report", "bulletin", "update", "samachar", "press conference",
            "politics", "election", "government", "khabar"
        ],
        "path_hints": ["news", "samachar", "breaking"]
    },
    {
        "genre": "Entertainment",
        "clean_title": "Entertainment & Shorts",
        "keywords": [
            "entertainment", "lifestyle", "celebrity", "influencer", "show", "talk show", "shorts"
        ],
        "path_hints": []
    }
]


def classify_genre(
    analysis_json: Any = None,
    fallback_text: str = "",
    filename: str = "",
    file_path: str = "",
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    channel_name: str = ""
) -> str:
    """
    Classify a video into an accurate content genre / category using
    all available video metadata, file path folder names, and AI analysis.
    """
    info = classify_genre_full(
        analysis_json=analysis_json,
        fallback_text=fallback_text,
        filename=filename,
        file_path=file_path,
        title=title,
        description=description,
        tags=tags,
        channel_name=channel_name
    )
    return info["genre"]


def classify_genre_full(
    analysis_json: Any = None,
    fallback_text: str = "",
    filename: str = "",
    file_path: str = "",
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    channel_name: str = ""
) -> Dict[str, Any]:
    """
    Returns full genre dictionary with genre name, clean playlist title,
    matched keywords, and classification score.
    """
    # 1. Parse analysis_json
    data = {}
    if isinstance(analysis_json, str) and analysis_json.strip():
        try:
            data = json.loads(analysis_json)
        except Exception:
            data = {}
    elif isinstance(analysis_json, dict):
        data = analysis_json

    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    # 2. Build multi-tiered text buckets
    # Path & folder names (highest priority human intent)
    path_text = f"{file_path} {filename} {fallback_text}".lower().replace("-", " ").replace("_", " ")

    # Direct title & core metadata
    direct_title = " ".join([
        title or "",
        meta.get("title", ""),
        data.get("title", ""),
        Path(filename or fallback_text).stem if (filename or fallback_text) else ""
    ]).lower()

    # Tags & Hashtags
    all_tags = []
    if tags:
        all_tags.extend(tags if isinstance(tags, list) else str(tags).split(","))
    if meta.get("tags"):
        all_tags.extend(meta["tags"] if isinstance(meta["tags"], list) else str(meta["tags"]).split(","))
    if meta.get("hashtags"):
        all_tags.extend(meta["hashtags"] if isinstance(meta["hashtags"], list) else str(meta["hashtags"]).split())
    if data.get("tags"):
        all_tags.extend(data["tags"] if isinstance(data["tags"], list) else str(data["tags"]).split(","))
    tags_text = " ".join(str(t).strip().lower().lstrip("#") for t in all_tags)

    # Narrative & Summary text
    narrative_text = " ".join([
        description or "",
        meta.get("description", ""),
        meta.get("summary", ""),
        data.get("summary", ""),
        data.get("visual_summary", ""),
        data.get("transcript", ""),
        data.get("style", ""),
        data.get("setting", ""),
        " ".join(data.get("subjects", []) if isinstance(data.get("subjects"), list) else [])
    ]).lower()

    channel_text = str(channel_name or "").lower()

    # 3. Score every genre in taxonomy
    best_genre = None
    highest_score = 0
    best_matched_keyword = ""

    for g in GENRE_TAXONOMY:
        score = 0
        matched_kw = ""

        # Score folder path hints (strong indicator of dedicated channel/folder)
        for hint in g.get("path_hints", []):
            if hint in path_text:
                score += 15
                matched_kw = f"path:{hint}"

        # Score channel name hint
        for hint in g.get("path_hints", []):
            if hint in channel_text:
                score += 10
                matched_kw = matched_kw or f"channel:{hint}"

        # Score keywords in title and tags
        for kw in g["keywords"]:
            # Word boundary regex for single words, direct substring for multi-word phrases
            is_multi = " " in kw
            if is_multi:
                if kw in direct_title:
                    score += 12
                    matched_kw = matched_kw or kw
                elif kw in path_text:
                    score += 8
                    matched_kw = matched_kw or kw
                elif kw in tags_text:
                    score += 6
                    matched_kw = matched_kw or kw
                elif kw in narrative_text:
                    score += 3
                    matched_kw = matched_kw or kw
            else:
                pattern = r"\b" + re.escape(kw) + r"\b"
                if re.search(pattern, direct_title):
                    score += 8
                    matched_kw = matched_kw or kw
                elif re.search(pattern, tags_text):
                    score += 5
                    matched_kw = matched_kw or kw
                elif re.search(pattern, path_text):
                    score += 4
                    matched_kw = matched_kw or kw
                elif re.search(pattern, narrative_text):
                    score += 2
                    matched_kw = matched_kw or kw

        if score > highest_score:
            highest_score = score
            best_genre = g
            best_matched_keyword = matched_kw

    # If nothing scored, fallback to channel-themed Entertainment
    if not best_genre or highest_score == 0:
        best_genre = GENRE_TAXONOMY[-1]  # Entertainment

    return {
        "genre": best_genre["genre"],
        "clean_title": best_genre["clean_title"],
        "score": highest_score,
        "matched_keyword": best_matched_keyword
    }


def clean_series_name(raw_name: str) -> str:
    """
    Clean up noisy prefixes, bundle suffixes, channel tags, and punctuation
    to produce a crisp, readable series name suitable for YouTube playlists.
    e.g. '1000+ Viral Desi Food Stories By Deehastore' -> 'Viral Desi Food Stories'
    """
    s = str(raw_name or "").strip()
    # Strip file extensions if present
    s = re.sub(r"\.(?:mp4|mkv|mov|avi|webm)$", "", s, flags=re.IGNORECASE).strip()
    # Strip leading promotional quantities like '1000+ ' or '500 '
    s = re.sub(r"^\d+\+?\s*", "", s).strip()
    # Strip creator bundle tags like 'by deehastore', 'by alpha', 'WorkLinc.in', '@handle'
    s = re.sub(r"(?i)\s+by\s+[A-Za-z0-9_.-]+", "", s).strip()
    s = re.sub(r"(?i)\bWorkLinc(?:\.in)?\b", "", s).strip()
    s = re.sub(r"@\w+", "", s).strip()
    s = re.sub(r"(?i)\s+bundle\b", "", s).strip()
    # Strip leading/trailing dashes, pipes, underscores, dots
    s = re.sub(r"^[\s\-–—_.:|#]+|[\s\-–—_.:|#]+$", "", s).strip()
    # Replace multiple spaces with single space
    s = re.sub(r"\s+", " ", s).strip()
    return s or raw_name


def extract_series_and_part(filename: str, title: str = "") -> Tuple[bool, str, float, str]:
    """
    Detect whether a video is part of a multi-part sequence or series.
    Returns:
        (is_series: bool, series_name: str, part_number: float, pattern_type: str)

    Recognizes:
    - Explicit Part/Episode/Chapter/Bhag: 'Kahani Part 2', 'Movie Recap Ep. 3', 'भाग 1', 'P2'
    - Parentheses numbers: 'Viral Desi Food Stories (12).mp4'
    - Leading sequence numbers: '0520 Alpha P. Meme Page Reels.mp4'
    - Trailing alphanumeric series markers: 'Movie Recaps Febric203.mp4'
    """
    stem = Path(filename or "").stem.strip()
    t = (title or "").strip()

    # 1. Check title for explicit Part / Episode / Bhag / Chapter
    # e.g. "Alien Ka Secret Exposed Ho Gaya! | Movie Recap Part 3"
    m_title = re.search(
        r'(.*?)(?:\s*[-–—|:]\s*)?(?:\b(?:part|pt|episode|ep|chapter|ch|vol|volume|भाग)[\s._#-]*0*([0-9]+(?:\.[0-9]+)?)\b|\b[pP][\s._#-]*0*([0-9]+)\b)(.*)',
        t, re.IGNORECASE
    )
    if m_title:
        prefix = (m_title.group(1) or '').strip(' -–—|:')
        suffix = (m_title.group(4) or '').strip(' -–—|:')
        raw_part = m_title.group(2) or m_title.group(3)
        part_num = float(raw_part) if raw_part else 1.0
        combined = f"{prefix} {suffix}".strip(' -–—|:') or prefix or stem
        clean_s = clean_series_name(combined)
        return True, clean_s, part_num, "title_part"

    # 2. Check filename for explicit Part / Episode
    m_file_part = re.search(
        r'(.*?)(?:\s*[-–—_.]\s*)?(?:\b(?:part|pt|episode|ep|chapter|ch|vol|volume|भाग)[\s._#-]*0*([0-9]+(?:\.[0-9]+)?)\b|\b[pP][\s._#-]*0*([0-9]+)\b)(.*)',
        stem, re.IGNORECASE
    )
    if m_file_part:
        prefix = (m_file_part.group(1) or '').strip(' -–—_.')
        suffix = (m_file_part.group(4) or '').strip(' -–—_.')
        raw_part = m_file_part.group(2) or m_file_part.group(3)
        part_num = float(raw_part) if raw_part else 1.0
        combined = f"{prefix} {suffix}".strip(' -–—_.') or prefix or stem
        clean_s = clean_series_name(combined)
        return True, clean_s, part_num, "file_part"

    # 3. Check filename with trailing parentheses number: e.g. 'Food Stories (12).mp4'
    m_paren = re.search(r'^(.*?)\s*[\(\[]\s*0*([0-9]+)\s*[\)\]]$', stem)
    if m_paren:
        series = clean_series_name(m_paren.group(1).strip())
        part_num = float(m_paren.group(2))
        return True, series, part_num, "paren_num"

    # 4. Check filename with leading index digits: e.g. '0520 Alpha P. Meme Page Reels'
    m_lead = re.search(r'^0*([0-9]{2,})\s*[-_.]?\s+(.+)$', stem)
    if m_lead:
        part_num = float(m_lead.group(1))
        series = clean_series_name(m_lead.group(2).strip())
        return True, series, part_num, "lead_num"

    # 5. Check filename with trailing word+digits: e.g. 'Movie Recaps Febric203'
    m_trail = re.search(r'^(.*?[A-Za-z]+)\s*0*([0-9]{1,4})$', stem)
    if m_trail:
        series = clean_series_name(m_trail.group(1).strip())
        part_num = float(m_trail.group(2))
        return True, series, part_num, "trail_num"

    return False, clean_series_name(stem), 0.0, "none"


# Module-level cache for channel playlists to avoid redundant API queries
_PLAYLIST_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_PLAYLIST_ITEMS_CACHE: Dict[str, Set[str]] = {}


def get_channel_existing_playlists(channel_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Return all playlists for a channel, using an in-memory cache to save API quota."""
    import time as _time
    now = _time.time()
    if not force_refresh and channel_id in _PLAYLIST_CACHE:
        cached_time, pls = _PLAYLIST_CACHE[channel_id]
        if now - cached_time < 300:  # 5 min cache
            return pls

    try:
        pls = list_playlists(channel_id)
        _PLAYLIST_CACHE[channel_id] = (now, pls)
        return pls
    except Exception as err:
        log.warning("[PLAYLIST] Failed to fetch channel playlists for %s: %s", channel_id, err)
        if channel_id in _PLAYLIST_CACHE:
            return _PLAYLIST_CACHE[channel_id][1]
        return []


def get_playlist_video_ids(channel_id: str, playlist_id: str, force_refresh: bool = False) -> Set[str]:
    """Return set of YouTube video IDs already present in a given playlist."""
    cache_key = f"{channel_id}:{playlist_id}"
    if not force_refresh and cache_key in _PLAYLIST_ITEMS_CACHE:
        return _PLAYLIST_ITEMS_CACHE[cache_key]

    video_ids = set()
    try:
        from .youtube import get_youtube_service
        yt = get_youtube_service(channel_id)
        token = None
        while True:
            resp = yt.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=token or ""
            ).execute()
            for item in resp.get("items", []):
                vid = (item.get("contentDetails") or {}).get("videoId")
                if vid:
                    video_ids.add(vid)
            token = resp.get("nextPageToken")
            if not token:
                break
    except Exception as err:
        log.warning("[PLAYLIST] Could not list playlist items for %s: %s", playlist_id, err)

    _PLAYLIST_ITEMS_CACHE[cache_key] = video_ids
    return video_ids


def find_matching_playlist(
    channel_id: str,
    genre: str,
    clean_title: str,
    series_name: str = "",
    filename: str = "",
    title: str = "",
    existing_playlists: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """
    Search channel's existing playlists to find a match for series or genre.
    Strictly reuses existing playlists so no duplicates are created on YouTube.
    """
    playlists = existing_playlists if existing_playlists is not None else get_channel_existing_playlists(channel_id)
    if not playlists:
        return None

    g_lower = genre.lower().strip()
    c_lower = clean_title.lower().strip()
    s_lower = series_name.lower().strip() if series_name else ""
    content_text = f"{filename} {title} {series_name}".lower()

    # Priority 0: Direct keyword match between existing playlist title and video content
    # (e.g. video has 'Meme' and channel already has 'Meme' playlist)
    for pl in playlists:
        p_title = ((pl.get("snippet") or {}).get("title") or "").strip().lower()
        if not p_title:
            continue
        # Extract meaningful terms from playlist title
        p_words = [w for w in re.split(r"[\s&—–\-]+", p_title) if len(w) >= 4]
        for w in p_words:
            if w in content_text:
                return pl

    # Priority 1: Match series name if this video belongs to a series
    if s_lower:
        for pl in playlists:
            p_title = ((pl.get("snippet") or {}).get("title") or "").strip().lower()
            if not p_title:
                continue
            if p_title == s_lower or s_lower in p_title or p_title in s_lower:
                return pl

    # Priority 2: Exact or starts-with match with clean title or genre
    for pl in playlists:
        p_title = ((pl.get("snippet") or {}).get("title") or "").strip().lower()
        if not p_title:
            continue
        if p_title == c_lower or p_title == g_lower or p_title.startswith(c_lower) or p_title.startswith(g_lower):
            return pl

    # Priority 3: Keyword / taxonomy match
    matched_genre_def = next((g for g in GENRE_TAXONOMY if g["genre"] == genre), None)
    genre_keywords = matched_genre_def["keywords"] if matched_genre_def else []

    for pl in playlists:
        p_title = ((pl.get("snippet") or {}).get("title") or "").strip().lower()
        if not p_title:
            continue
        for kw in genre_keywords[:20]:
            if len(kw) >= 4 and kw in p_title:
                return pl

    return None


def assign_playlist(
    channel_id: str,
    youtube_video_id: str,
    analysis_json: Any = None,
    channel_name: str = "",
    filename: str = "",
    file_path: str = "",
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    create_series_playlists: bool = False,
    existing_playlists: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Classify video into its true genre and add to an existing matching playlist,
    or create a clean, dedicated category playlist on the channel.
    Also detects multi-part series to maintain chronological continuity.
    """
    # 1. Multi-part / Series Detection
    is_series, series_name, part_num, pattern_type = extract_series_and_part(filename=filename, title=title)

    # 2. Genre Classification
    genre_info = classify_genre_full(
        analysis_json=analysis_json,
        filename=filename,
        file_path=file_path,
        title=title,
        description=description,
        tags=tags,
        channel_name=channel_name
    )

    genre = genre_info["genre"]
    clean_title = genre_info["clean_title"]

    log.info("[PLAYLIST] Video '%s' (vid=%s): Genre='%s', Series=%s (Name='%s', Part=%.1f, Type=%s)",
             filename or title, youtube_video_id, genre, is_series, series_name, part_num, pattern_type)

    # 3. Retrieve Channel's Existing Playlists
    playlists = existing_playlists if existing_playlists is not None else get_channel_existing_playlists(channel_id)

    # 4. Find Match from Existing Playlists
    matched = find_matching_playlist(
        channel_id=channel_id,
        genre=genre,
        clean_title=clean_title,
        series_name=series_name if is_series else "",
        filename=filename,
        title=title,
        existing_playlists=playlists
    )

    created = False
    playlist_id = ""
    playlist_title = ""

    if matched:
        playlist_id = matched["id"]
        raw_title = (matched.get("snippet") or {}).get("title", clean_title)
        # Strip legacy channel name suffix if present (e.g. "Meme — ChannelName" -> "Meme")
        cleaned = re.sub(r"\s*[—–\-]\s*(?:[A-Za-z0-9_ ]+)?$", "", raw_title).strip()
        if cleaned and cleaned != raw_title and not dry_run:
            try:
                update_playlist(channel_id, playlist_id, cleaned)
                playlist_title = cleaned
                log.info("[PLAYLIST] Cleaned legacy channel name suffix from YouTube playlist '%s' -> '%s'", raw_title, cleaned)
            except Exception as up_err:
                log.warning("[PLAYLIST] Could not auto-rename legacy playlist '%s': %s", raw_title, up_err)
                playlist_title = raw_title
        else:
            playlist_title = cleaned or raw_title
        log.info("[PLAYLIST] Reusing existing playlist '%s' (id=%s)", playlist_title, playlist_id)
    else:
        # Determine title for new playlist
        if is_series and create_series_playlists and series_name:
            playlist_title = series_name
            desc = f"Complete series of {series_name}."
        else:
            playlist_title = clean_title
            desc = f"Official collection of {clean_title} videos."

        if not dry_run:
            result = create_playlist(channel_id, playlist_title, desc, "public")
            playlist_id = result["id"]
            created = True
            log.info("[PLAYLIST] Created new clean category playlist '%s' (id=%s)", playlist_title, playlist_id)
        else:
            playlist_id = f"dry_run_{clean_title.replace(' ', '_')}"
            created = True
            log.info("[PLAYLIST-DRY-RUN] Would create new playlist '%s'", playlist_title)

        # Update cache so subsequent videos in this batch immediately reuse this playlist!
        new_pl_item = {
            "id": playlist_id,
            "snippet": {"title": playlist_title, "description": desc}
        }
        if existing_playlists is not None:
            existing_playlists.append(new_pl_item)
        if channel_id in _PLAYLIST_CACHE:
            _PLAYLIST_CACHE[channel_id][1].append(new_pl_item)


    # 5. Insert video into playlist (checking for duplicates first)
    already_in_playlist = False
    if youtube_video_id and not dry_run:
        # Check if video is already inside this playlist on YouTube
        existing_vids = get_playlist_video_ids(channel_id, playlist_id)
        if youtube_video_id in existing_vids:
            already_in_playlist = True
            log.info("[PLAYLIST] Video %s is already inside playlist '%s' (%s). Skipping add.", youtube_video_id, playlist_title, playlist_id)
        else:
            try:
                add_to_playlist(channel_id, playlist_id, youtube_video_id)
                existing_vids.add(youtube_video_id)
                log.info("[PLAYLIST] Added video=%s to playlist=%s (%s)", youtube_video_id, playlist_id, playlist_title)
            except Exception as add_err:
                log.warning("[PLAYLIST] Could not add video %s to playlist %s: %s", youtube_video_id, playlist_id, add_err)
    elif dry_run:
        log.info("[PLAYLIST-DRY-RUN] Would add video=%s to playlist=%s (%s)", youtube_video_id, playlist_id, playlist_title)

    return {
        "genre": genre,
        "clean_title": clean_title,
        "is_series": is_series,
        "series_name": series_name,
        "part_number": part_num,
        "playlist_id": playlist_id,
        "playlist_title": playlist_title,
        "created": created,
        "already_in_playlist": already_in_playlist
    }

