# Implementation Plan — AI Thumbnail Generator + Smart Playlist Manager

## Key decisions made during exploration

**DB migration pattern** — `database.py` uses `PRAGMA table_info` + `if col not in cols` guards before every `ALTER TABLE ADD COLUMN`. Both new columns follow this exact pattern.

**Gemini call pattern** — `gemini.py` exposes `load_credentials(conn)`, `_base_headers(creds, project)`, and `_parse_json(text)`. `thumbnail.py` and `playlist.py` import these helpers instead of re-implementing OAuth.

**youtube.py additions** — three new free-standing functions appended at the bottom; they mirror the style of `list_channel_videos` / `get_video_stats`.

**No new type hints** — the existing codebase uses `str | None` union syntax only in Pydantic models and FastAPI route signatures; plain functions use no annotations. New code matches this.

**Thumbnail rendering** — Pillow draws directly onto a 1280×720 canvas. No external image APIs. Font is tried in order: `C:/Windows/Fonts/arialbd.ttf` → `/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf` → `ImageFont.load_default()`.

**Playlist classification** — pure string-matching against the `analysis_json` fields `summary`, `style`, `subjects`, `tags`, and `title`; no extra Gemini call. Genre list: Comedy, Horror, Drama, Action, News, Music, Education, Motivation, Roast, Reaction, Story, Shorts.

**Static mount** — thumbnails served via `GET /api/video/{video_id}/thumbnail` using `FileResponse`; the `/thumbnails/` folder sits at the workspace root alongside `/data/` and `/logs/`.

---

- [ ] 1. Add `Pillow>=10,<12` to `requirements.txt`.
      Append the line after the existing `requests` entry.
      Files: `requirements.txt`
      Verify: `python -c "import PIL; print(PIL.__version__)"` in the venv — prints a version ≥ 10.

- [ ] 2. Add `thumbnail_path` and `youtube_playlist_id` columns to `video_inventory` via safe migration in `init_db()`.
      After the existing `vcols` migration block (the one that adds `hashtags`, `tags`, `ai_status`, etc.), append two more guarded `ALTER TABLE` calls:
      ```python
      if "thumbnail_path" not in vcols:
          conn.execute("ALTER TABLE video_inventory ADD COLUMN thumbnail_path TEXT DEFAULT ''")
      if "youtube_playlist_id" not in vcols:
          conn.execute("ALTER TABLE video_inventory ADD COLUMN youtube_playlist_id TEXT DEFAULT ''")
      ```
      Files: `app/database.py`
      Verify: `python -c "from app.database import init_db; init_db(); import sqlite3; c=sqlite3.connect('data/uploader.db'); print([r[1] for r in c.execute('PRAGMA table_info(video_inventory)')])"` — output list includes `thumbnail_path` and `youtube_playlist_id`.

- [ ] 3. Create `app/thumbnail.py` — AI Thumbnail Generator module.
      Exports one public function: `generate_thumbnails(video_id, analysis_json, filename, connection_id=None)`.
      
      **Internal structure:**
      - `_load_font(size)` — tries `C:/Windows/Fonts/arialbd.ttf`, then `/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf`, then `ImageFont.load_default()`.
      - `_get_concept(analysis_json, filename, connection_id)` — if `connection_id` is set, call Gemini (same `load_credentials` / `_base_headers` / `requests.post` / `_parse_json` pattern from `gemini.py`) with a short prompt asking for `{"title": "...", "color_scheme": "yellow_red|blue_purple|green_orange", "emoji": "🔥", "layout": "top_bottom|center"}`. Falls back to `{"title": filename[:40].upper(), "color_scheme": "yellow_red", "emoji": "▶", "layout": "center"}` on any exception.
      - `_draw_thumbnail(concept, variant_idx)` — creates a 1280×720 RGBA image:
        - Gradient background with `ImageDraw` drawing horizontal scan-lines that lerp between two colors based on `color_scheme` + `variant_idx` offset.
        - Dark vignette: draw a semi-transparent black rectangle over the bottom 30% (y > 504).
        - Title text: wrap at 20 chars, center-align, draw white text with a 4-px black offset shadow using `_load_font(90)` for the main title; reduce to `_load_font(60)` if text is long.
        - Emoji accent in top-right corner using `_load_font(100)`.
      - `generate_thumbnails(video_id, analysis_json, filename, connection_id=None)`:
        1. `Path("thumbnails").mkdir(exist_ok=True)` (path relative to `BASE_DIR` from `config.py`).
        2. Get concept via `_get_concept`.
        3. Loop `variant_idx` in `range(3)`: call `_draw_thumbnail(concept, variant_idx)`, save as `thumbnails/video_{video_id}_v{variant_idx+1}.png`.
        4. Return list of saved paths (strings).
      
      Files: `app/thumbnail.py`
      Verify: The file is importable: `python -c "from app.thumbnail import generate_thumbnails; print('ok')"` — prints `ok`.

- [ ] 4. Create `app/playlist.py` — Smart Playlist Manager module.
      Exports two public functions: `classify_genre(analysis_json)` and `assign_playlist(channel_id, youtube_video_id, analysis_json, channel_name)`.

      **`classify_genre(analysis_json)`:**
      - Parse the JSON string; look at `summary`, `style`, `subjects`, `tags`, `title` fields.
      - Case-insensitive keyword scan. Genre priority order (first match wins):
        - `Horror` — keywords: horror, scary, dark, ghost, haunted, bhoot, dar
        - `Comedy` — keywords: comedy, funny, laugh, joke, prank, hilarious, hasna
        - `Roast` — keywords: roast, troll, exposed, savage
        - `Reaction` — keywords: reaction, react, reacting, response
        - `Music` — keywords: music, song, gana, rap, beat, melody, lyrics
        - `News` — keywords: news, breaking, report, update, viral, latest
        - `Education` — keywords: learn, education, tutorial, how to, explain, facts, sikh
        - `Motivation` — keywords: motivation, inspire, success, mindset, hustle
        - `Drama` — keywords: drama, kahani, story, emotional, feel, relationship
        - `Action` — keywords: action, fight, battle, war, challenge
        - `Shorts` — keywords: shorts, short video, clip
        - `Story` — default fallback
      - Return the matched genre string.

      **`assign_playlist(channel_id, youtube_video_id, analysis_json, channel_name)`:**
      1. Call `classify_genre(analysis_json)` to get `genre`.
      2. Call `get_youtube_service(channel_id)` from `app.auth`.
      3. Call `list_playlists(channel_id)` (new function in `youtube.py`, see item 5). Check if a playlist whose title starts with `genre` exists.
      4. If not found, call `create_playlist(channel_id, f"{genre} — {channel_name}", f"Auto-created playlist for {genre} content.", "public")` (new function in `youtube.py`).
      5. Call `add_to_playlist(channel_id, playlist_id, youtube_video_id)` (new function in `youtube.py`).
      6. Return `{"genre": genre, "playlist_id": playlist_id, "playlist_title": title, "created": bool}`.
      
      Files: `app/playlist.py`
      Verify: `python -c "from app.playlist import classify_genre; print(classify_genre('{\"summary\":\"funny video\"}'))"` — prints `Comedy`.

- [ ] 5. Add three new functions to `app/youtube.py`.
      Append after `extract_hash_marker`:
      ```python
      def list_playlists(channel_id):
          """Return all playlists owned by the channel."""
          youtube = get_youtube_service(channel_id)
          out = []; token = None
          while True:
              resp = youtube.playlists().list(
                  part="snippet,status", mine=True, maxResults=50,
                  pageToken=token or ""
              ).execute()
              out.extend(resp.get("items", []))
              token = resp.get("nextPageToken")
              if not token:
                  break
          return out

      def create_playlist(channel_id, title, description, privacy="public"):
          """Create a new YouTube playlist and return the API item."""
          youtube = get_youtube_service(channel_id)
          resp = youtube.playlists().insert(
              part="snippet,status",
              body={
                  "snippet": {"title": title, "description": description},
                  "status": {"privacyStatus": privacy},
              }
          ).execute()
          return resp

      def add_to_playlist(channel_id, playlist_id, youtube_video_id):
          """Insert a video into a playlist and return the API item."""
          youtube = get_youtube_service(channel_id)
          resp = youtube.playlistItems().insert(
              part="snippet",
              body={
                  "snippet": {
                      "playlistId": playlist_id,
                      "resourceId": {"kind": "youtube#video", "videoId": youtube_video_id},
                  }
              }
          ).execute()
          return resp
      ```
      Files: `app/youtube.py`
      Verify: `python -c "from app.youtube import list_playlists, create_playlist, add_to_playlist; print('ok')"` — prints `ok`.

- [ ] 6. Add new imports and four API endpoints to `app/main.py`.
      
      **Imports to add** (at the top, after existing imports):
      ```python
      from .thumbnail import generate_thumbnails
      from .playlist import assign_playlist
      from .youtube import list_playlists as yt_list_playlists
      ```
      Also add `update_video` import to the `database` import block if not already present (it is already imported).

      **Endpoints to add** (append before the final blank line):

      ```python
      @app.post("/api/video/{video_id}/thumbnail")
      async def api_generate_thumbnail(video_id: int, request: Request):
          row = get_inventory_by_id(video_id)
          if not row:
              raise HTTPException(status_code=404, detail="Video not found.")
          conns = [c for c in list_ai_connections("gemini") if c.get("status") != "logged_out"]
          connection_id = conns[0]["id"] if conns else None
          try:
              paths = await asyncio.to_thread(
                  generate_thumbnails, video_id,
                  row.get("analysis_json") or "",
                  row.get("filename") or "",
                  connection_id
              )
              primary = paths[0] if paths else ""
              if primary:
                  update_video(video_id, thumbnail_path=primary)
              log.info("[THUMB] Generated %d thumbnails for video_id=%s", len(paths), video_id)
              return {"ok": True, "paths": paths, "count": len(paths)}
          except Exception as exc:
              logging.exception("Thumbnail generation failed")
              raise HTTPException(status_code=400, detail=str(exc))

      @app.get("/api/video/{video_id}/thumbnail")
      def api_get_thumbnail(video_id: int):
          row = get_inventory_by_id(video_id)
          if not row:
              raise HTTPException(status_code=404, detail="Video not found.")
          path_str = row.get("thumbnail_path") or ""
          if not path_str:
              raise HTTPException(status_code=404, detail="No thumbnail generated yet.")
          path = Path(path_str)
          if not path.is_absolute():
              from .config import BASE_DIR
              path = BASE_DIR / path
          if not path.exists():
              raise HTTPException(status_code=404, detail="Thumbnail file not found on disk.")
          return FileResponse(str(path), media_type="image/png", filename=path.name)

      @app.post("/api/video/{video_id}/playlist")
      async def api_assign_playlist(video_id: int, request: Request):
          row = get_inventory_by_id(video_id)
          if not row:
              raise HTTPException(status_code=404, detail="Video not found.")
          if not row.get("youtube_video_id"):
              raise HTTPException(status_code=400, detail="Video must be uploaded to YouTube before assigning a playlist.")
          channel = get_channel(row["channel_id"])
          if not channel:
              raise HTTPException(status_code=404, detail="Channel not found.")
          try:
              result = await asyncio.to_thread(
                  assign_playlist,
                  row["channel_id"],
                  row["youtube_video_id"],
                  row.get("analysis_json") or "",
                  channel.get("channel_name") or ""
              )
              update_video(video_id, youtube_playlist_id=result["playlist_id"])
              log.info("[PLAYLIST] Assigned video_id=%s to playlist=%s genre=%s created=%s",
                       video_id, result["playlist_id"], result["genre"], result.get("created"))
              return {"ok": True, **result}
          except Exception as exc:
              logging.exception("Playlist assignment failed")
              raise HTTPException(status_code=400, detail=str(exc))

      @app.post("/api/channel/{channel_id}/playlists/sync")
      async def api_sync_playlists(channel_id: str, request: Request):
          if not get_channel(channel_id):
              raise HTTPException(status_code=404, detail="Channel not found.")
          try:
              items = await asyncio.to_thread(yt_list_playlists, channel_id)
              playlists = [
                  {
                      "playlist_id": x.get("id"),
                      "title": (x.get("snippet") or {}).get("title", ""),
                      "privacy": (x.get("status") or {}).get("privacyStatus", ""),
                  }
                  for x in items
              ]
              return {"ok": True, "playlists": playlists, "count": len(playlists)}
          except Exception as exc:
              logging.exception("Playlist sync failed")
              raise HTTPException(status_code=400, detail=str(exc))
      ```
      
      Also add `thumbnail_path` and `youtube_playlist_id` as keyword arguments to `update_video` calls — but only where we need them. The `update_video` function in `database.py` already accepts `**kwargs` (verify in step 7 if needed).
      
      Files: `app/main.py`
      Verify: `python -c "import ast, sys; ast.parse(open('app/main.py').read()); print('syntax ok')"` — prints `syntax ok`.

- [ ] 7. Verify `update_video` in `database.py` accepts `thumbnail_path` and `youtube_playlist_id`.
      Read the `update_video` function; if it uses a fixed column allowlist, add both new column names to the allowlist. If it already uses dynamic kwargs, no change is needed.
      Files: `app/database.py` (only if the allowlist check is needed)
      Verify: `python -c "from app.database import update_video; print('ok')"` — prints `ok`.

- [ ] 8. Add `thumbnails/` output folder creation to app startup in `app/config.py`.
      Add `THUMBNAILS_DIR = BASE_DIR / "thumbnails"` alongside the other `*_DIR` constants and include it in the `for d in (...)` mkdir loop.
      Files: `app/config.py`
      Verify: `python -c "from app.config import THUMBNAILS_DIR; print(THUMBNAILS_DIR)"` — prints the expected path.

- [ ] 9. Modify `web/library.html` — add Thumbnail and Playlist buttons per video row.

      In the `render()` function's template string, the current `<td>` for Actions is:
      ```js
      <td><button class="btn btn-secondary" onclick="playVideo(${v.id},'${esc(v.filename)}')">▶ Play</button> ${v.status==='uploaded'?`<button class="btn btn-primary" onclick="showAnalytics(${v.id},'${esc(v.filename)}')">📊 Analytics</button>`:''}</td>
      ```
      Replace the Actions `<td>` with:
      ```js
      <td>
        <button class="btn btn-secondary" onclick="playVideo(${v.id},'${esc(v.filename)}')">▶ Play</button>
        ${v.status==='uploaded'?`<button class="btn btn-primary" onclick="showAnalytics(${v.id},'${esc(v.filename)}')">📊 Analytics</button>`:''}
        <button class="btn btn-secondary" onclick="showThumbnail(${v.id})">🖼️ Thumbnail</button>
        ${v.status==='uploaded'?`<button class="btn btn-secondary" onclick="assignPlaylist(${v.id},'${esc(v.filename)}')">📂 Playlist</button>`:''}
      </td>
      ```

      Add a thumbnail modal (before the closing `</div></div></div>` of the app):
      ```html
      <div id="thumbModal" class="modal">
        <div class="modal-box" style="width:min(900px,100%)">
          <div class="row">
            <h2>Thumbnail Variants</h2>
            <button class="btn btn-secondary" onclick="closeThumb()">Close</button>
          </div>
          <div id="thumbBody">Generating…</div>
        </div>
      </div>
      ```

      Add JS functions before `init().catch(...)`:
      ```js
      async function showThumbnail(id) {
        document.getElementById('thumbBody').innerHTML = 'Generating thumbnails…';
        document.getElementById('thumbModal').classList.add('open');
        try {
          const d = await api('/api/video/' + id + '/thumbnail', {method: 'POST'});
          const imgs = (d.paths || []).map((p, i) =>
            `<div style="margin-bottom:12px">
               <img src="/api/video/${id}/thumbnail?v=${Date.now()}" style="max-width:100%;border-radius:8px" alt="Thumbnail variant ${i+1}"/>
               <div><a class="btn btn-secondary" href="/api/video/${id}/thumbnail" download="thumbnail_${id}.png" style="margin-top:8px;display:inline-block">⬇ Download</a></div>
             </div>`
          ).join('');
          document.getElementById('thumbBody').innerHTML = imgs || '<p>No thumbnails generated.</p>';
        } catch(e) {
          document.getElementById('thumbBody').innerHTML = `<div class="notice error">${esc(e.message)}</div>`;
        }
      }
      function closeThumb() {
        document.getElementById('thumbModal').classList.remove('open');
      }
      async function assignPlaylist(id, name) {
        try {
          const d = await api('/api/video/' + id + '/playlist', {method: 'POST'});
          const msg = d.created
            ? `Created and added to new playlist: ${d.playlist_title} (${d.genre})`
            : `Added to existing playlist: ${d.playlist_title} (${d.genre})`;
          setMessage('body', msg, 'success');
        } catch(e) {
          setMessage('body', e.message, 'error');
        }
      }
      ```

      Files: `web/library.html`
      Verify: Open the file and confirm the new `thumbModal` div and the `showThumbnail`/`assignPlaylist` functions are present. The server must start without errors: `python -c "from app.main import app; print('app ok')"`.

- [ ] 10. Full integration check — import all new modules from the app context.
      Run: `python -c "from app.main import app; from app.thumbnail import generate_thumbnails; from app.playlist import assign_playlist, classify_genre; from app.youtube import list_playlists, create_playlist, add_to_playlist; print('all imports ok')"` from the workspace root.
      Verify: Prints `all imports ok` with no `ImportError` or `SyntaxError`.
