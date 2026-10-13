from pathlib import Path
import shutil
import PySide6

root=Path(SPECPATH)
qt=Path(PySide6.__file__).parent
binaries=[]
for name in ('ffmpeg','ffprobe'):
    path=shutil.which(name)
    if not path: raise RuntimeError('Не найден '+name)
    binaries.append((str(Path(path).resolve()),'bin'))
for name in ('VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
    if (qt/name).is_file():binaries.append((str(qt/name),'.'))
a=Analysis([str(root/'run_app.py')],pathex=[str(root)],binaries=binaries,datas=[],hiddenimports=[],
           hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False,optimize=0)
# Qt использует встроенную Windows ICU с экспортами без суффиксов версий.
# Poppler в PATH может подменить её несовместимым icuuc.dll. Используем DLL ОС.
a.binaries=[b for b in a.binaries if Path(b[0]).name.lower() not in ('icuuc.dll','icudt78.dll')]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='СжатьВидео',debug=False,
        bootloader_ignore_signals=False,strip=False,upx=True,console=False,
        disable_windowed_traceback=False,argv_emulation=False,target_arch=None,
        codesign_identity=None,entitlements_file=None)
