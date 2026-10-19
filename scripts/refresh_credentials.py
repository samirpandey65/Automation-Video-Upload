#!/usr/bin/env python3
"""
CLI script to refresh all credentials in credentials/vault.json.
Usage:
    python scripts/refresh_credentials.py
"""
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.credential_manager import refresh_all_credentials

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Refresh all credentials in vault")
    parser.add_argument("--vault", default=None, help="Path to custom vault.json")
    args = parser.parse_args()

    print("[*] Starting credential refresh...")
    results = refresh_all_credentials(args.vault)
    print("\n[*] Refresh Results Summary:")
    for name, res in results.items():
        status = res.get("status")
        if status == "ok":
            detail = res.get("detail", {})
            exp = detail.get("expires_in_days")
            exp_str = f"{exp} days" if exp is not None else "n/a"
            print(f"  [OK] {name}: SUCCESS (Expires in: {exp_str})")
        else:
            print(f"  [FAIL] {name}: FAILED -> {res.get('error')}")
