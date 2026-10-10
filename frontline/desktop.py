"""Frontline as a desktop app: the API runs in-process and the UI opens in a native window.

    pythonw -m frontline.desktop            # launch (what the desktop shortcut runs)
    python  -m frontline.desktop --install  # create Desktop + Start-menu shortcuts
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ICON_ICO = ROOT / "static" / "frontline.ico"
ICON_PNG = ROOT / "static" / "frontline.png"
APP_ID = "Frontline.ResearchRadar"  # keep in sync with config.APP_ID
TITLE = "Frontline"


def _redirect_output() -> None:
    """pythonw has no console, so stdout/stderr are None; send logs to a file instead."""
    if sys.stdout is None or sys.stderr is None:
        DATA.mkdir(parents=True, exist_ok=True)
        log = open(DATA / "frontline.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log


def _message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x40)  # MB_ICONINFORMATION


def _single_instance():
    """Hold an exclusive lock for the app's lifetime; None if another window already has it."""
    import msvcrt

    DATA.mkdir(parents=True, exist_ok=True)
    fh = open(DATA / ".desktop.lock", "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    return fh


def _windows_identity() -> None:
    """Own taskbar identity + a registered app ID so toasts say "Frontline" with our icon."""
    import winreg

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, TITLE)
        winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(ICON_PNG))


def _set_window_icon(window) -> None:
    """pywebview on Windows inherits pythonw's icon; swap in ours once the window exists."""
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    try:
        hwnd = int(window.native.Handle.ToInt64())  # the WinForms form behind the webview
    except Exception:
        return
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
    for which, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
        h = user32.LoadImageW(None, str(ICON_ICO), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if h:
            user32.SendMessageW(hwnd, WM_SETICON, which, h)


def _pick_port(preferred: int = 8000) -> int:
    """Use 8000 when free (so notification links keep working), otherwise any free port."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def run() -> None:
    _redirect_output()
    lock = _single_instance()
    if lock is None:
        _message_box("Frontline is already open — check your taskbar.")
        return
    _windows_identity()

    port = _pick_port()
    # must be set before frontline.config is imported
    os.environ["FRONTLINE_DESKTOP"] = "1"
    os.environ["PUBLIC_URL"] = f"http://localhost:{port}"

    import uvicorn
    import webview

    from frontline.app import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = threading.Thread(target=server.run, name="frontline-api", daemon=True)
    thread.start()
    for _ in range(150):  # wait up to 15 s for the API
        if server.started or not thread.is_alive():
            break
        time.sleep(0.1)
    if not server.started:
        _message_box(f"Frontline couldn't start its server.\nSee {DATA / 'frontline.log'} for details.")
        return

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # paper/PDF links open in your browser
    webview.settings["ALLOW_DOWNLOADS"] = True                 # "Export as Markdown"
    window = webview.create_window(
        TITLE, f"http://127.0.0.1:{port}/", width=1380, height=900, min_size=(420, 560),
        background_color="#e9e9e5", text_select=True,
    )
    window.events.shown += lambda: _set_window_icon(window)
    # persistent storage keeps UI prefs (theme, sort) between launches
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA / "webview"))

    server.should_exit = True
    thread.join(timeout=5)
    lock.close()


# ---------------------------------------------------------------- shortcuts

_SHORTCUT_PS = r"""
$shell = New-Object -ComObject WScript.Shell
$targets = @(
  [Environment]::GetFolderPath('Desktop'),
  (Join-Path ([Environment]::GetFolderPath('Programs')) '')
)
foreach ($dir in $targets) {
  $lnk = $shell.CreateShortcut((Join-Path $dir 'Frontline.lnk'))
  $lnk.TargetPath = $env:FL_TARGET
  $lnk.Arguments = '-m frontline.desktop'
  $lnk.WorkingDirectory = $env:FL_ROOT
  $lnk.IconLocation = "$env:FL_ICON,0"
  $lnk.Description = 'Frontline research radar'
  $lnk.Save()
  Write-Output (Join-Path $dir 'Frontline.lnk')
}
"""


def install_shortcuts() -> None:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        sys.exit(f"pythonw.exe not found next to {sys.executable}")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SHORTCUT_PS],
        env={**os.environ, "FL_TARGET": str(pythonw), "FL_ROOT": str(ROOT), "FL_ICON": str(ICON_ICO)},
        capture_output=True, text=True, check=True,
    )
    print("Created shortcuts:\n  " + "\n  ".join(out.stdout.splitlines()))


if __name__ == "__main__":
    if "--install" in sys.argv:
        install_shortcuts()
    else:
        run()
