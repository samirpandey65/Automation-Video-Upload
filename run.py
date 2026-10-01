import os
import socket
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def _is_free_threaded() -> bool:
    """Return True when running a CPython free-threaded (3.13t+) build."""
    try:
        return hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()
    except Exception:
        return False


def _venv_python() -> Path:
    path = BASE_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    return path / ("python.exe" if os.name == "nt" else "python")


def _run_python_command(args):
    """Run a Python launcher command and return its exit code."""
    return subprocess.call(args)


def _create_venv():
    venv_dir = BASE_DIR / ".venv"
    if venv_dir.exists():
        import shutil
        print("[SETUP] Removing incomplete virtual environment...")
        shutil.rmtree(venv_dir, ignore_errors=True)

    # On Windows, prefer the standard CPython build. Python 3.13 free-threaded
    # builds (python3.13t) are currently incompatible with the cffi/cryptography
    # dependency chain used by Google authentication.
    if os.name == "nt" and _is_free_threaded():
        print("[SETUP] Free-threaded CPython detected. Searching for standard CPython...")
        for launcher_args in (["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"]):
            try:
                if _run_python_command(launcher_args + ["-m", "venv", str(venv_dir)]) == 0:
                    return _venv_python()
            except (FileNotFoundError, OSError):
                continue
        raise RuntimeError(
            "A standard (non-free-threaded) CPython 3.11-3.13 installation is required. "
            "Your current Python is a free-threaded build. Install standard Python 3.13 "
            "or 3.12, then run this launcher again."
        )

    subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
    return _venv_python()


def _install_dependencies(venv_python: Path):
    requirements = BASE_DIR / "requirements.txt"
    print("[SETUP] Installing application dependencies...")
    subprocess.check_call([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([str(venv_python), "-m", "pip", "install", "-r", str(requirements)])


def ensure_runtime():
    """Create/repair a local venv and ensure the runtime dependencies are installed."""
    venv_python = _venv_python()

    if not venv_python.exists():
        print("[SETUP] Local Python environment not found. Creating .venv...")
        venv_python = _create_venv()
        _install_dependencies(venv_python)
        return venv_python

    # A previous dependency installation may have failed, leaving a valid-looking
    # venv without uvicorn. Verify imports and repair the environment automatically.
    check = subprocess.run(
        [str(venv_python), "-c", "import fastapi, uvicorn"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        print("[SETUP] Existing .venv is incomplete. Repairing dependencies...")
        _install_dependencies(venv_python)

    return venv_python


def delegate_to_venv():
    venv_python = ensure_runtime()
    try:
        same = Path(sys.executable).resolve().samefile(Path(venv_python).resolve())
    except Exception:
        same = str(Path(sys.executable).resolve()).lower() == str(Path(venv_python).resolve()).lower()
    if not same:
        sys.exit(subprocess.call([str(venv_python), str(Path(__file__).resolve())] + sys.argv[1:]))


delegate_to_venv()

import uvicorn

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    host = os.environ.get("HOST", "0.0.0.0")
    #host = os.environ.get("HOST", "127.0.0.1")
    local_ip = get_local_ip()

    print("\n" + "=" * 58)
    print("  YouTube Local Auto Uploader (Lan Mode)")
    print("=" * 58)
    print(f"  > Local Machine:   http://localhost:{port}")
    print(f"  > Local Machine:   http://127.0.0.1:{port}")
    print(f"  > Network / Wi-Fi:  http://{local_ip}:{port}")
    print(f"  > Bound Address:   http://{host}:{port}")
    print("=" * 58)
    print(f"  Open http://{local_ip}:{port} on other PCs/phones on your Wi-Fi!\n")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
    )
