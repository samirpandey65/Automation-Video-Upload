import hashlib
from pathlib import Path
from .config import VIDEO_EXTENSIONS

def sha256_file(path, chunk_size=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def scan_folder(folder):
    base = Path(folder).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"Folder does not exist: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"Not a folder: {base}")

    files = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
            stat = p.stat()
            files.append({
                "path": str(p.resolve()),
                "filename": p.name,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            })
    return sorted(files, key=lambda x: x["filename"].lower())

def choose_folder_dialog(initial_dir=""):
    import subprocess
    folder = ""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(
            master=root,
            title="Select Video Folder",
            initialdir=initial_dir if initial_dir and Path(initial_dir).exists() else None
        )
        root.destroy()
        if folder:
            return str(Path(folder).resolve())
    except Exception:
        pass

    if not folder:
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Select Video Folder'; "
                "if($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){ Write-Output $f.SelectedPath }"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    return folder or ""
