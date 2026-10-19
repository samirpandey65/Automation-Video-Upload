#!/usr/bin/env python3
"""
Phase 11 Verification Suite:
- Batch Playlist Retrofit Engine
- Multi-Part Series Detection & Pacing
- Strict Sequence Ordering (Part 1 -> Part 2)
- Content Isolation & Prevention of Mixed Content
- Existing YouTube Playlist Reuse & Duplicate Protection
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.playlist import extract_series_and_part, clean_series_name, find_matching_playlist, classify_genre_full
from app.scheduler import select_next_continuity_video
from scripts.retrofit_playlists import run_playlist_retrofit

def test_series_and_part_extraction():
    print("[TEST 1] Series & Multi-Part Extraction...")
    cases = [
        # (filename, title, expected_is_series, expected_part)
        ("Alien Ka Secret.mp4", "Alien Ka Secret Exposed Ho Gaya! | Movie Recap Part 3", True, 3.0),
        ("Machine That Predicts How You Die Part 2.mp4", "", True, 2.0),
        ("1000+ Viral Desi Food Stories By Deehastore (9).mp4", "", True, 9.0),
        ("0520 Alpha P. Meme Page Reels.mp4", "", True, 520.0),
        ("Movie Recaps Febric203.mp4", "", True, 203.0),
        ("Jadui Chappal Episode 04.mp4", "Jadui Chappal Ep 4", True, 4.0),
        ("Panchatantra Kahani Bhag 2.mp4", "", True, 2.0),
        ("Single Story Video.mp4", "A Lone Hero Adventure", False, 0.0),
    ]

    for fname, title, exp_is_series, exp_part in cases:
        is_s, s_name, p_num, p_type = extract_series_and_part(fname, title)
        print(f"   '{fname}' | '{title}' => Series: {is_s}, Name: '{s_name}', Part: {p_num} ({p_type})")
        assert is_s == exp_is_series, f"Expected is_series={exp_is_series} for {fname}"
        if exp_is_series:
            assert p_num == exp_part, f"Expected part={exp_part}, got {p_num} for {fname}"

    print("[OK] [TEST 1 PASSED] Series & Part Extraction verified successfully!")


def test_existing_playlist_matching():
    print("\n[TEST 2] Existing YouTube Playlist Reuse (Zero Duplicates)...")
    existing_playlists = [
        {"id": "PL_FOOD", "snippet": {"title": "Food & Recipes"}},
        {"id": "PL_MEME", "snippet": {"title": "Meme"}},
        {"id": "PL_SPORTS", "snippet": {"title": "Football & Sports"}},
    ]

    # Test direct keyword matching with existing playlists
    matched_meme = find_matching_playlist(
        channel_id="CH_TEST",
        genre="Comedy & Humor",
        clean_title="Comedy & Fun",
        series_name="Alpha P. Meme Page Reels",
        filename="0520 Alpha P. Meme Page Reels.mp4",
        title="Funny meme reaction",
        existing_playlists=existing_playlists
    )
    assert matched_meme is not None and matched_meme["id"] == "PL_MEME", f"Expected PL_MEME, got {matched_meme}"
    print("   [OK] Successfully matched video with 'Meme' to existing 'Meme' playlist [PL_MEME]")

    matched_food = find_matching_playlist(
        channel_id="CH_TEST",
        genre="Food & Recipes",
        clean_title="Food & Recipes",
        series_name="Viral Desi Food Stories",
        filename="Viral Desi Food Stories (12).mp4",
        title="Delicious Chaat",
        existing_playlists=existing_playlists
    )
    assert matched_food is not None and matched_food["id"] == "PL_FOOD", f"Expected PL_FOOD, got {matched_food}"
    print("   [OK] Successfully matched video with 'Food' to existing 'Food & Recipes' playlist [PL_FOOD]")

    matched_sports = find_matching_playlist(
        channel_id="CH_TEST",
        genre="Football & Sports",
        clean_title="Football & Sports",
        series_name="Football Highlights",
        filename="Ronaldo Free Kick Skills.mp4",
        title="Best Ronaldo Goals",
        existing_playlists=existing_playlists
    )
    assert matched_sports is not None and matched_sports["id"] == "PL_SPORTS", f"Expected PL_SPORTS, got {matched_sports}"
    print("   [OK] Successfully matched video with sports to existing 'Football & Sports' playlist [PL_SPORTS]")

    print("[OK] [TEST 2 PASSED] Existing playlist matching verified successfully!")


def test_scheduler_continuity_and_sequencing():
    print("\n[TEST 3] Scheduler Multi-Part Continuity & Content Isolation...")
    # Simulation pool:
    # Series A: Part 1, Part 2, Part 3 (Genre: Animated Stories)
    # Series B: Part 1, Part 2 (Genre: Food & Recipes)
    candidates = [
        {"id": 101, "filename": "Jadui Kahani Part 3.mp4", "title": "Jadui Kahani Part 3", "tags": "kahani"},
        {"id": 102, "filename": "Jadui Kahani Part 1.mp4", "title": "Jadui Kahani Part 1", "tags": "kahani"},
        {"id": 103, "filename": "Jadui Kahani Part 2.mp4", "title": "Jadui Kahani Part 2", "tags": "kahani"},
        {"id": 201, "filename": "Desi Food (1).mp4", "title": "Desi Food Part 1", "tags": "food recipe"},
        {"id": 202, "filename": "Desi Food (2).mp4", "title": "Desi Food Part 2", "tags": "food recipe"},
    ]

    # Test 3A: If no last upload, starts with Part 1 of first group
    first_choice = select_next_continuity_video("FAKE_CH_EMPTY", candidates)
    is_s, s_name, p_num, _ = extract_series_and_part(first_choice["filename"], first_choice["title"])
    print(f"   Initial queue choice: '{first_choice['title']}' (Series: {s_name}, Part: {p_num})")
    assert p_num == 1.0, f"Expected Part 1 first, got {p_num}"

    # Test 3B: If Part 1 was previously uploaded, select_next_continuity_video MUST pick Part 2!
    # Mock last uploaded Part 1 by inserting into SQLite with a temporary test channel
    from app.database import get_conn, upsert_video, update_video, upsert_channel, delete_channel
    test_ch = "TEST_CONTINUITY_CH_999"
    upsert_channel(test_ch, "Continuity Test Channel", "@test", "test@example.com", "token_test")
    try:
        # Simulate upload of Part 1
        item1 = upsert_video(test_ch, "/test/path/p1.mp4", "Jadui Kahani Part 1.mp4", "sha_p1", 100, 1000.0, "uploaded")
        update_video(item1["id"], status="uploaded", youtube_video_id="yt_p1_vid", title="Jadui Kahani Part 1")

        remaining = [c for c in candidates if c["id"] != 102] # Part 1 is done
        next_pick = select_next_continuity_video(test_ch, remaining)
        is_s, s_name, p_num, _ = extract_series_and_part(next_pick["filename"], next_pick["title"])
        print(f"   After Part 1 uploaded => Next selected: '{next_pick['title']}' (Part: {p_num})")
        assert p_num == 2.0, f"Expected Part 2 to be selected next, got {p_num}"

        # Now simulate upload of Part 2
        item2 = upsert_video(test_ch, "/test/path/p2.mp4", "Jadui Kahani Part 2.mp4", "sha_p2", 100, 2000.0, "uploaded")
        update_video(item2["id"], status="uploaded", youtube_video_id="yt_p2_vid", title="Jadui Kahani Part 2")

        remaining_after_p2 = [c for c in candidates if c["id"] not in (102, 103)]
        next_pick_p3 = select_next_continuity_video(test_ch, remaining_after_p2)
        is_s, s_name, p_num, _ = extract_series_and_part(next_pick_p3["filename"], next_pick_p3["title"])
        print(f"   After Part 2 uploaded => Next selected: '{next_pick_p3['title']}' (Part: {p_num})")
        assert p_num == 3.0, f"Expected Part 3 to be selected next, got {p_num}"

        # Now simulate upload of Part 3 (Series A is complete)
        item3 = upsert_video(test_ch, "/test/path/p3.mp4", "Jadui Kahani Part 3.mp4", "sha_p3", 100, 3000.0, "uploaded")
        update_video(item3["id"], status="uploaded", youtube_video_id="yt_p3_vid", title="Jadui Kahani Part 3")

        # Now check transition to next content type (Series B: Food)
        remaining_after_series_a = [c for c in candidates if c["id"] not in (101, 102, 103)]
        next_type_pick = select_next_continuity_video(test_ch, remaining_after_series_a)
        is_s, s_name, p_num, _ = extract_series_and_part(next_type_pick["filename"], next_type_pick["title"])
        print(f"   After Series A completed => Next type selected: '{next_type_pick['title']}' (Part: {p_num})")
        assert p_num == 1.0 and "Food" in next_type_pick["title"], f"Expected Desi Food Part 1, got {next_type_pick}"

    finally:
        delete_channel(test_ch)


    print("[OK] [TEST 3 PASSED] Continuity sequencing and content isolation verified successfully!")


def test_retrofit_dry_run_execution():
    print("\n[TEST 4] Batch Playlist Retrofit Dry-Run Execution...")
    res = run_playlist_retrofit(dry_run=True, limit=5)
    print(f"   Result status:       {res['status']}")
    print(f"   Videos evaluated:    {res['total_evaluated']}")
    print(f"   Videos assigned:     {res['total_assigned']}")
    print(f"   Playlists reused:    {res['playlists_reused']}")
    print(f"   Playlists created:   {res['playlists_created']}")
    print(f"   Series detected:     {res['series_detected']}")

    assert res["status"] in ("completed", "quota_limited"), f"Expected completed, got {res['status']}"
    assert res["total_evaluated"] > 0, "Expected evaluated videos > 0"
    assert len(res["channel_reports"]) > 0, "Expected channel reports > 0"
    print("[OK] [TEST 4 PASSED] Batch Playlist Retrofit dry-run executed successfully!")


if __name__ == "__main__":
    print("=" * 65)
    print(" RUNNING PHASE 11 VERIFICATION SUITE")
    print("=" * 65)
    test_series_and_part_extraction()
    test_existing_playlist_matching()
    test_scheduler_continuity_and_sequencing()
    test_retrofit_dry_run_execution()
    print("\n" + "=" * 65)
    print(" [SUCCESS] ALL PHASE 11 TESTS PASSED PERFECTLY!")
    print("=" * 65)

