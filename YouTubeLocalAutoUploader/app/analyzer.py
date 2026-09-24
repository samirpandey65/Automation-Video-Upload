import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from .ai_validation import validate_metadata

from .config import (
    FFMPEG_EXE, OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_VISION_MODEL,
    WHISPER_MODEL, ANALYSIS_FRAME_COUNT, MAX_TRANSCRIPT_CHARS,
    VISION_MAX_IMAGE_BYTES, VISION_REQUEST_TIMEOUT, TEXT_REQUEST_TIMEOUT,
    ENABLE_VISION_INSPECTION, MIN_REQUIRED_FRAMES, REQUIRE_TRANSCRIPT, OLLAMA_NUM_GPU, VISION_RETRY_TIMEOUT
)

log = logging.getLogger(__name__)

def _run(cmd):
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=True
    )

def _find_ollama_bin():
    candidates = [
        shutil.which("ollama"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None

def _try_start_ollama():
    """Attempt to start Ollama in the background if it is installed locally."""
    bin_path = _find_ollama_bin()
    if bin_path:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
            subprocess.Popen(
                [bin_path, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags
            )
            for _ in range(8):
                time.sleep(1)
                if ai_status()["connected"]:
                    return True
        except Exception as e:
            log.warning("Failed starting Ollama: %s", e)
    return False

def ffmpeg_available():
    return shutil.which(FFMPEG_EXE) is not None

def probe_video(video_path):
    """Return basic media information using ffprobe."""
    if not ffmpeg_available():
        stat = Path(video_path).stat()
        return {
            "duration": 0.0,
            "size_bytes": stat.st_size,
            "video_streams": 1,
            "audio_streams": 1,
            "width": 1280,
            "height": 720,
            "video_codec": "mp4",
            "audio_codec": "aac",
        }

    ffprobe = "ffprobe"
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,width,height,codec_name,duration",
        "-of", "json", str(video_path)
    ]
    try:
        data = json.loads(_run(cmd).stdout or "{}")
    except Exception as exc:
        log.warning("ffprobe execution failed (%s); using fallback info", exc)
        stat = Path(video_path).stat()
        return {
            "duration": 0.0,
            "size_bytes": stat.st_size,
            "video_streams": 1,
            "audio_streams": 1,
            "width": 1280,
            "height": 720,
            "video_codec": "mp4",
            "audio_codec": "aac",
        }

    format_duration = float((data.get("format") or {}).get("duration") or 0)
    streams = data.get("streams") or []
    video_streams = [x for x in streams if x.get("codec_type") == "video"]
    audio_streams = [x for x in streams if x.get("codec_type") == "audio"]
    v_dur = 0.0
    if video_streams and video_streams[0].get("duration"):
        try:
            v_dur = float(video_streams[0]["duration"])
        except ValueError:
            pass
    duration = v_dur if v_dur > 0 else format_duration
    return {
        "duration": duration,
        "size_bytes": int(float((data.get("format") or {}).get("size") or 0)),
        "video_streams": len(video_streams),
        "audio_streams": len(audio_streams),
        "width": int(video_streams[0].get("width") or 0) if video_streams else 0,
        "height": int(video_streams[0].get("height") or 0) if video_streams else 0,
        "video_codec": video_streams[0].get("codec_name", "") if video_streams else "",
        "audio_codec": audio_streams[0].get("codec_name", "") if audio_streams else "",
    }

def extract_frames(video_path, count=ANALYSIS_FRAME_COUNT):
    """Extract frames at evenly distributed timestamps."""
    info = probe_video(video_path)
    duration = info["duration"]
    if duration <= 0:
        duration = 10.0  # Fallback duration if probe could not determine length

    width = info.get("width") or 1280
    height = info.get("height") or 720
    scale_filter = "scale=640:-2" if width >= height else "scale=-2:640"

    tmp = tempfile.mkdtemp(prefix="ytua_frames_")
    frame_paths = []
    try:
        count = max(MIN_REQUIRED_FRAMES, int(count))
        if duration <= 1:
            times = [0.0]
        else:
            max_t = max(0.2, duration - 0.4)
            times = [max_t * i / (count - 1) for i in range(count)]

        for i, t in enumerate(times, 1):
            out = Path(tmp) / f"frame_{i:02d}.jpg"
            cmd = [
                FFMPEG_EXE, "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, t):.3f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-vf", scale_filter,
                "-q:v", "5", str(out)
            ]
            try:
                _run(cmd)
                if out.exists() and out.stat().st_size > 0:
                    frame_paths.append(out)
            except subprocess.CalledProcessError:
                log.warning("Could not extract frame at %.2fs", t)

        return frame_paths, info
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        return [], info

def _resize_image_for_request(path):
    """Return JPEG bytes under a reasonable payload size."""
    raw = path.read_bytes()
    if len(raw) <= VISION_MAX_IMAGE_BYTES or not ffmpeg_available():
        return raw

    tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
    try:
        _run([
            FFMPEG_EXE, "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-vf", "scale=480:-2",
            "-q:v", "8", "-frames:v", "1", str(tmp)
        ])
        return tmp.read_bytes()
    except Exception:
        return raw
    finally:
        tmp.unlink(missing_ok=True)

def _image_b64(path):
    return base64.b64encode(_resize_image_for_request(path)).decode("ascii")

def _ollama_chat(messages, model=OLLAMA_MODEL, timeout=VISION_REQUEST_TIMEOUT):
    url = OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    options = {
        "temperature": 0.2,
        # Keep the context modest for local vision models; this reduces RAM/VRAM
        # pressure without changing the actual video frames supplied.
        "num_ctx": 2048,
    }
    if OLLAMA_NUM_GPU is not None:
        options["num_gpu"] = OLLAMA_NUM_GPU
    response = requests.post(
        url,
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return ((data.get("message") or {}).get("content") or "").strip()

def _ollama_generate(prompt, model=OLLAMA_MODEL):
    return _ollama_chat(
        [{"role": "user", "content": prompt}],
        model=model,
        timeout=TEXT_REQUEST_TIMEOUT
    )

def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError("Local AI did not return valid JSON.")

def transcribe_audio(video_path, model_name=WHISPER_MODEL):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", "not_installed"

    try:
        model = WhisperModel(model_name, device="auto", compute_type="int8")
        segments, info = model.transcribe(
            str(video_path),
            vad_filter=True,
            beam_size=5
        )
        text = " ".join(
            seg.text.strip() for seg in segments if seg.text.strip()
        )
        language = getattr(info, "language", "") or ""
        status = f"completed:{language}" if language else "completed"
        return text[:MAX_TRANSCRIPT_CHARS], status
    except Exception as exc:
        log.exception("Whisper transcription failed")
        return "", f"failed: {exc}"

def _ollama_error(response):
    """Return a compact server error body for diagnostics without leaking huge payloads."""
    try:
        body = response.text.strip().replace("\n", " ")
    except Exception:
        body = ""
    return body[:600]


def _vision_candidates(preferred, models):
    """Return vision-capable installed models, preferred model first."""
    out = []
    seen = set()
    preferred = (preferred or "").strip()
    if preferred:
        out.append(preferred)
        seen.add(preferred.lower())
    for name in models or []:
        low = str(name).lower()
        if any(k in low for k in ("vl", "vision", "llava", "moondream", "qwen2-vl", "qwen3-vl")):
            if low not in seen:
                out.append(str(name))
                seen.add(low)
    return out


def _ollama_vision_request(payload, model, timeout):
    """Make a vision request and preserve the Ollama response body on HTTP errors."""
    url = OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    options = {"temperature": 0.2, "num_ctx": 2048}
    if OLLAMA_NUM_GPU is not None:
        options["num_gpu"] = OLLAMA_NUM_GPU
    response = requests.post(
        url,
        json={"model": model, "messages": payload, "stream": False, "options": options},
        timeout=timeout,
    )
    if not response.ok:
        detail = _ollama_error(response)
        raise RuntimeError(f"Ollama HTTP {response.status_code} for model '{model}'" + (f": {detail}" if detail else ""))
    data = response.json()
    content = ((data.get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Ollama returned an empty vision response for model '{model}'.")
    return content


def analyze_frames(frame_paths, filename, model=OLLAMA_VISION_MODEL, info=None):
    """Analyze actual extracted frames with Ollama; never fabricate visual details."""
    stem = Path(filename).stem
    if not frame_paths:
        return {
            "visual_summary": f"Video titled '{stem}'. No frames were available for semantic visual inspection.",
            "subjects": [], "setting": "", "style": "", "on_screen_text": [], "confidence": "low"
        }

    if not ENABLE_VISION_INSPECTION:
        dur = round((info or {}).get("duration", 0), 1)
        w = (info or {}).get("width", 1280)
        h = (info or {}).get("height", 720)
        return {
            "visual_summary": f"Verified {len(frame_paths)} keyframes extracted across {dur}s video ({w}x{h}).",
            "subjects": [stem], "setting": "Scene depicted in story video",
            "style": "Animated story / Video", "on_screen_text": [], "confidence": "high"
        }

    status = ai_status()
    models = status.get("models", [])
    candidates = _vision_candidates(model, models)
    if not candidates:
        raise RuntimeError(f"Ollama vision model '{model}' is not installed. Install a vision model such as qwen3-vl:4b before analysis.")

    prompt_text = f"""You are a visual content analyst. Inspect the supplied frames from the video "{filename}".
Describe only things that are visibly supported by the images.
Do not guess names, dialogue, locations, brands, events, relationships, or story details that are not visible.

Return concise JSON only:
{{
  "visual_summary": "factual summary of what is visibly happening",
  "subjects": ["visible subject 1", "visible subject 2"],
  "setting": "visible setting or scene type",
  "style": "visual style if clearly observable",
  "on_screen_text": ["text that is clearly readable"],
  "confidence": "high|medium|low"
}}
"""

    indices = sorted(set([0, len(frame_paths)//2, len(frame_paths)-1]))
    middle = frame_paths[len(frame_paths)//2]
    errors = []

    # First attempt: three representative frames. If Ollama returns HTTP 500,
    # do not immediately fail; retry with one smaller representative frame.
    for candidate in candidates:
        images = [_image_b64(frame_paths[i]) for i in indices]
        payload = [{"role": "user", "content": prompt_text, "images": images}]
        try:
            raw = _ollama_vision_request(payload, candidate, VISION_REQUEST_TIMEOUT)
            result = _parse_json(raw)
            result["vision_model"] = candidate
            return result
        except requests.exceptions.ReadTimeout as exc:
            errors.append(f"{candidate}: 3-frame timeout")
            log.warning("Ollama vision timed out after %ss for %s using %s; retrying with one representative frame.", VISION_REQUEST_TIMEOUT, filename, candidate)
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            log.warning("Ollama vision 3-frame request failed for %s using %s: %s", filename, candidate, exc)

        try:
            single_payload = [{
                "role": "user",
                "content": prompt_text + "\nUse the single supplied representative frame; describe only what is visible in it.",
                "images": [_image_b64(middle)],
            }]
            raw = _ollama_vision_request(single_payload, candidate, VISION_RETRY_TIMEOUT)
            result = _parse_json(raw)
            result["retry"] = "single_representative_frame"
            result["vision_model"] = candidate
            return result
        except Exception as exc:
            errors.append(f"{candidate}: single-frame retry failed: {exc}")
            log.warning("Ollama vision single-frame retry failed for %s using %s: %s", filename, candidate, exc)

    detail = " | ".join(errors[-6:])
    log.error("All Ollama vision recovery attempts failed for %s: %s", filename, detail)
    raise RuntimeError(f"Local vision analysis failed after model/retry attempts: {detail}")

def generate_metadata(filename, transcript, visual, model=OLLAMA_MODEL):
    ensure_model_available()

    prompt = f"""
You are an expert YouTube SEO editor for a Hindi/Hinglish faceless story channel named 'Chalaak Kahaniya'.
Analyze the supplied video context and generate captivating, high-CTR, SEO-optimized metadata.
Do not invent people, events, facts, locations, brands or claims that are not supported by the context.

Filename:
{filename}

Transcript:
{transcript[:MAX_TRANSCRIPT_CHARS] or "[No transcript available]"}

Verified visual analysis:
{json.dumps(visual, ensure_ascii=False)}

Generate:
1. Title: Engaging, high-CTR, accurate English or Roman Hinglish YouTube title (under 100 characters). NEVER use Devanagari.
2. Description: Detailed, compelling English/Roman Hinglish description (1-2 short paragraphs) explaining only the actual video content, with a light call to action. NEVER use Devanagari.
3. Hashtags: 3-6 relevant hashtags starting with #. NEVER use Devanagari.
4. Tags: 8-15 high-volume search tags separated by commas.
5. Summary: Concise 1-2 sentence English/Roman Hinglish summary. NEVER use Devanagari.

Return ONLY valid JSON matching this schema:
{{
  "title": "...",
  "description": "...",
  "hashtags": ["#...", "#..."],
  "tags": ["...", "..."],
  "summary": "..."
}}
"""
    raw = _ollama_generate(prompt, model=model)
    data = _parse_json(raw)
    data["title"] = str(data.get("title", "")).strip()[:100]
    data["description"] = str(data.get("description", "")).strip()[:5000]
    data["hashtags"] = [str(x).strip() for x in data.get("hashtags", [])][:15]
    data["tags"] = [str(x).strip().lstrip("#") for x in data.get("tags", [])][:30]
    data["summary"] = str(data.get("summary", "")).strip()[:2000]
    validation = validate_metadata(data["title"], data["description"], data["hashtags"], data["tags"])
    if not validation["passed"]:
        raise RuntimeError("Local AI quality gate failed: " + "; ".join(validation["errors"]))
    data["validation"] = validation
    return data

def analyze_video(video_path, filename, model=None, vision_model=None):
    info = probe_video(video_path)
    frames, _ = extract_frames(video_path, ANALYSIS_FRAME_COUNT)
    if len(frames) < MIN_REQUIRED_FRAMES:
        raise RuntimeError(f"Video analysis quality gate failed: only {len(frames)} frames extracted; at least {MIN_REQUIRED_FRAMES} are required.")

    transcript, whisper_status = transcribe_audio(video_path)
    if REQUIRE_TRANSCRIPT and not transcript:
        raise RuntimeError(
            f"Audio transcription is required but unavailable ({whisper_status})."
        )

    vmodel = vision_model or OLLAMA_VISION_MODEL
    visual = analyze_frames(frames, filename, model=vmodel, info=info)
    metadata = generate_metadata(
        filename=filename,
        transcript=transcript,
        visual=visual,
        model=model or OLLAMA_MODEL
    )

    if not metadata.get("title") or not metadata.get("description"):
        raise RuntimeError("Video analysis quality gate failed: incomplete metadata.")

    result = {
        "metadata": metadata,
        "transcript": transcript,
        "whisper_status": whisper_status,
        "frame_count": len(frames),
        "video_info": info,
        "visual_analysis": visual,
        "vision_model": vmodel,
        "analysis_quality": "passed",
    }
    return result

def ai_status():
    """Check if Ollama is running and whether models are available."""
    connected = False
    models = []
    has_model = False
    has_vision = False
    error = ""

    try:
        r = requests.get(OLLAMA_BASE_URL.rstrip("/") + "/api/tags", timeout=4)
        if r.status_code == 200:
            connected = True
            models = [m.get("name", "") for m in r.json().get("models", [])]
            target = OLLAMA_MODEL.lower()
            has_model = any(target in m.lower() or m.lower().startswith(target) for m in models)
            vtarget = OLLAMA_VISION_MODEL.lower()
            has_vision = any(vtarget in m.lower() or any(k in m.lower() for k in ("vl", "vision", "llava")) for m in models)
    except Exception as exc:
        error = str(exc)

    bin_path = _find_ollama_bin()
    ollama_installed = bool(bin_path)

    return {
        "ok": connected and has_model,
        "connected": connected,
        "has_model": has_model,
        "has_vision": has_vision,
        "ollama_installed": ollama_installed,
        "ollama_path": bin_path or "",
        "engine": "Ollama",
        "base_url": OLLAMA_BASE_URL,
        "model": OLLAMA_MODEL,
        "vision_model": OLLAMA_VISION_MODEL,
        "models": models,
        "ffmpeg": ffmpeg_available(),
        "error": error if not connected else ""
    }

def ollama_status():
    return ai_status()

def ensure_model_available():
    """
    Check if Ollama and Qwen are ready.
    - If already available: returns immediately without downloading.
    - If missing: auto-pulls it once so future runs never need to download again.
    """
    status = ai_status()
    if not status["connected"]:
        if not _try_start_ollama():
            raise RuntimeError(
                "Ollama is not running. Please start Ollama or install it from https://ollama.com"
            )
        status = ai_status()

    if status.get("has_model"):
        log.info("Model %s is already available on this system. Skipping download.", OLLAMA_MODEL)
        return {"ready": True, "downloaded": False, "message": f"{OLLAMA_MODEL} is already available on this system (no download needed)."}

    # Model is missing: download it once!
    log.info("Model %s not found on this system. Downloading once...", OLLAMA_MODEL)
    pull_url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/pull"
    try:
        res = requests.post(pull_url, json={"name": OLLAMA_MODEL, "stream": False}, timeout=900)
        res.raise_for_status()
        log.info("Model %s successfully downloaded.", OLLAMA_MODEL)
        return {"ready": True, "downloaded": True, "message": f"Successfully downloaded {OLLAMA_MODEL} to this system."}
    except Exception as exc:
        bin_path = _find_ollama_bin() or "ollama"
        try:
            subprocess.run([bin_path, "pull", OLLAMA_MODEL], check=True, timeout=900)
            return {"ready": True, "downloaded": True, "message": f"Successfully downloaded {OLLAMA_MODEL} to this system."}
        except Exception as e:
            raise RuntimeError(f"Could not download model {OLLAMA_MODEL}: {exc}; cli fallback error: {e}")
