import sys
sys.stdout.reconfigure(encoding='utf-8')

from app.thumbnail import _clean_title_hook

# Test 1: Hook-first Title engine & Sanitizer
t1 = _clean_title_hook("Lead immediately with the emotional peak: 'Little boy negotiates free burger'")
assert t1 == "Little boy negotiates free burger", f"Failed prompt leak test: got {t1!r}"

t2 = _clean_title_hook("Cute Baby Feeds Cow — The Moment Everything Changed! ⚡")
assert t2 == "Cute Baby Feeds Cow", f"Failed template suffix test: got {t2!r}"

t3 = _clean_title_hook("Emotional Vegetable Funeral Story! (Full Story)")
assert t3 == "Emotional Vegetable Funeral Story!", f"Failed full story suffix test: got {t3!r}"

t4 = _clean_title_hook("100 Days Weight Loss Challenge Gone Wrong! 😂")
assert t4 == "100 Days Weight Loss Challenge Gone Wrong! 😂", f"Failed numeric hook preservation: got {t4!r}"

t5 = _clean_title_hook("The Shocking Truth About What ACTUALLY Happened? Smart Boy Prank")
assert t5 == "Smart Boy Prank", f"Failed prefix collapse: got {t5!r}"
print("✅ Test 1: Title Hook & Anti-Boilerplate Sanitizer tests passed")

# Test 2: Old Video Bulk Audit
from app.optimizer import audit_old_videos
audit = audit_old_videos()
assert audit["total_scanned"] > 0, "No videos scanned"
assert audit["total_flagged"] > 0, "No flagged videos found"
print(f"✅ Test 2: Old Video Bulk Audit passed ({audit['total_scanned']} scanned, {audit['total_flagged']} flagged)")

# Test 3: Dual-Mode Smart Traffic Agent
from app.scheduler import get_smart_scheduler_status, get_smart_traffic_window_info
traffic = get_smart_traffic_window_info()
assert "score" in traffic, "Missing score in traffic"
status = get_smart_scheduler_status()
assert "mode" in status, "Missing mode in status"
assert "cross_channel" in status, "Missing cross_channel guardrails"
assert "channels" in status, "Missing channels in status"
print(f"✅ Test 3: Dual-Mode Smart Traffic Agent status passed (Traffic: {traffic['window_name']}, Score: {traffic['score']}/100)")

print("🎉 ALL PHASE 10 VERIFICATION TESTS PASSED SUCCESSFULLY!")
