"""Local installer. Does not change Mumble's activation or reconnect a server."""
from pathlib import Path
import os
import shutil
import winreg

base = Path(__file__).resolve().parent
target = Path(os.environ['APPDATA']) / 'Mumble' / 'Mumble' / 'Plugins'
target.mkdir(parents=True, exist_ok=True)
shutil.copy2(base / 'mumble_ru.dll', target / 'mumble_ru.dll')
with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\MumbleRU') as key:
    winreg.SetValueEx(key, 'Python', 0, winreg.REG_SZ, str(base / '.venv' / 'Scripts' / 'pythonw.exe'))
    winreg.SetValueEx(key, 'Script', 0, winreg.REG_SZ, str(base / 'overlay.py'))
print('Plugin installed:', target / 'mumble_ru.dll')
print('In Mumble: Configure > Settings > Plugins > Reload > enable Mumble RU and its Audio feature.')
print('The overlay will start/stop with Mumble. Start.vbs also opens it manually.')
