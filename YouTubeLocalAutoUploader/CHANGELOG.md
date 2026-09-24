# Changelog

## systemd-fixed-v3
- Fixed Google OAuth `webbrowser.Error: could not locate runnable browser`.
- YouTube and Gemini OAuth no longer try to launch a browser from the server process.
- OAuth `run_local_server()` now uses `open_browser=False`, making it safe for systemd/headless execution.
- The Google authorization URL is displayed by the OAuth library/logs so it can be opened manually from a browser.
- Do not run `start.sh` with `sudo`; the service is intended to run as the `samir` user.
