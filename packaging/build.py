"""Сборка нативного дистрибутива из monitor.py и перечисленных ресурсов."""
from __future__ import annotations

import argparse
import os
import platform
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / 'packaging'
APP = runpy.run_path(str(ROOT / 'claude_usage_manager' / '__init__.py'))
NAME = APP['APP_NAME']
VERSION = APP['APP_VERSION']
WHEELS = ('Pillow', 'openpyxl', 'et-xmlfile', 'packaging', 'PySide6-Essentials', 'shiboken6',
          'PyInstaller', 'pyinstaller-hooks-contrib')


def write_licenses(directory: Path, compiler: Path | None) -> None:
    shutil.copytree(PACKAGING / 'licenses', directory)
    python_license = next((p for p in (Path(sys.base_prefix) / 'LICENSE.txt',
                                       Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt')
                           if p.is_file()), None)
    if python_license is None:
        raise FileNotFoundError('В каталоге установленного Python отсутствует LICENSE.txt')
    shutil.copyfile(python_license, directory / 'Python.txt')
    for name in WHEELS:
        dist = metadata.distribution(name)
        target = directory / f'{name}-{dist.version}'
        target.mkdir()
        for path in dist.files or ():
            if any(p.endswith('.dist-info') for p in path.parts) and (
                'licenses' in path.parts or path.name.lower().startswith(('copying', 'licen', 'notice'))
            ):
                shutil.copyfile(dist.locate_file(path), target / path.name)
        if not any(target.iterdir()):
            raise FileNotFoundError(f'В поставке {name} {dist.version} отсутствует лицензия')
    if compiler:
        shutil.copyfile(compiler.with_name('license.txt'), directory / 'Inno-Setup.txt')


def build(output: Path, work: Path, compiler: Path | None) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    machine = platform.machine().lower()
    if sys.platform == 'win32':
        if machine not in ('amd64', 'x86_64') or sys.maxsize <= 2**32:
            raise ValueError('Для сборки Windows требуется Python x64')
        if not compiler or not compiler.is_file():
            raise FileNotFoundError('Укажи путь к ISCC.exe в параметре --iscc')
        artifact = f'AI-Limits-Monitor-{VERSION}-windows-x64-setup.exe'
    elif sys.platform == 'darwin' and machine == 'arm64':
        artifact = f'AI-Limits-Monitor-{VERSION}-macos-{machine}.zip'
    else:
        raise ValueError('Поддерживается сборка на Windows x64 и macOS на Apple Silicon')

    with tempfile.TemporaryDirectory(prefix='monitor-build-', dir=work) as tmp:
        stage = Path(tmp)
        notices = stage / 'licenses'
        write_licenses(notices, compiler)
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        environment.update(PYTHONNOUSERSITE='1', PYINSTALLER_CONFIG_DIR=str(stage / 'cache'),
                           AI_LIMITS_BUILD_LICENSES=str(notices), TEMP=str(stage), TMP=str(stage),
                           TMPDIR=str(stage))
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--log-level', 'WARN',
                        '--distpath', str(stage / 'dist'), '--workpath', str(stage / 'work'),
                        str(PACKAGING / 'monitor.spec')], cwd=ROOT, env=environment, check=True)
        if compiler:
            subprocess.run([str(compiler), '/Qp', f'/DAppVersion={VERSION}',
                            f'/DPayload={stage / "dist" / NAME}', f'/DBuildOutput={stage}',
                            str(PACKAGING / 'windows.iss')], env=environment, check=True)
        else:
            app = stage / 'dist' / f'{NAME}.app'
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
            subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                            str(app), str(stage / artifact)], check=True)

        # Публикуем полностью собранный файл. Рабочий и выходной каталоги могут быть на разных дисках.
        with tempfile.NamedTemporaryFile(dir=output, prefix=f'.{artifact}.', delete=False) as target:
            pending = Path(target.name)
        try:
            shutil.copyfile(stage / artifact, pending)
            pending.replace(output / artifact)
        finally:
            pending.unlink(missing_ok=True)
    return output / artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'release')
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.pytest_tmp' / 'packaging')
    parser.add_argument('--iscc', type=Path)
    args = parser.parse_args()
    config = Path(sys.prefix) / 'pyvenv.cfg'
    if not config.is_file() or 'include-system-site-packages = false' not in config.read_text().lower():
        parser.error('Собирай из отдельного виртуального окружения с requirements-build.txt')
    result = build(args.output_dir.resolve(), args.work_dir.resolve(),
                   args.iscc.resolve() if args.iscc else None)
    print(result)


if __name__ == '__main__':
    main()
