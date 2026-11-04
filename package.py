from pathlib import Path
import zipfile

base = Path(__file__).resolve().parent
with zipfile.ZipFile(base / 'MumbleRU.mumble_plugin', 'w', zipfile.ZIP_DEFLATED) as bundle:
    for name in ('manifest.xml', 'mumble_ru.dll'):
        bundle.write(base / name, name)

with zipfile.ZipFile(base.parent / 'MumbleRU-Windows-x64.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in base.rglob('*'):
        relative = file.relative_to(base)
        if file.is_file() and not any(part in ('.git', '.venv', '__pycache__', 'models', 'test-data', 'logs') for part in relative.parts) and file.suffix not in ('.lib', '.a', '.pdb', '.zip') and file.name not in ('settings.json', '.gitignore') and not file.name.startswith('.env'):
            archive.write(file, 'MumbleRU/' + relative.as_posix())
print(base.parent / 'MumbleRU-Windows-x64.zip')
