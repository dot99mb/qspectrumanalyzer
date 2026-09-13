#!/usr/bin/env python3
"""Build a Debian 13 package using system Qt/NumPy and private pure-Python vendors."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
VENDORS = ('Qt.py==2.0.5', 'soapy_power==1.6.1', 'simplesoapy==1.5.1', 'simplespectral==1.0.0')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='2.2.0+custom20260913-1')
    args = parser.parse_args()
    subprocess.run(['dpkg', '--validate-version', args.version], check=True)
    wheels = ROOT / 'build/deb-wheels'
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                    '--wheel-dir', str(wheels), *VENDORS], check=True)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qspectr-deb-') as temporary:
        stage = Path(temporary)
        app = stage / 'usr/lib/qspectrumanalyzer'
        app.mkdir(parents=True)
        shutil.copytree(ROOT / 'qspectrumanalyzer', app / 'qspectrumanalyzer',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        vendors = app / 'vendor'
        vendors.mkdir()
        manifests = {}
        licenses = stage / 'usr/share/doc/qspectrumanalyzer'
        licenses.mkdir(parents=True)
        shutil.copy(ROOT / 'LICENSE', licenses / 'copyright')
        for requirement in VENDORS:
            name, version = requirement.split('==')
            normalized = name.lower().replace('.', '_').replace('-', '_')
            wheel, = wheels.glob(normalized + '-' + version + '-*-none-any.whl')
            manifests[wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
            with ZipFile(wheel) as archive:
                archive.extractall(vendors)
                for entry in archive.namelist():
                    if entry.lower().endswith('/license'):
                        (licenses / (normalized + '-LICENSE')).write_bytes(archive.read(entry))
        writer = vendors / 'soapypower/writer.py'
        source = writer.read_text()
        if 'numpy.fromstring(' not in source and 'numpy.frombuffer(' not in source:
            raise RuntimeError('Unexpected soapy_power writer; inspect NumPy compatibility')
        writer.write_text(source.replace('numpy.fromstring(', 'numpy.frombuffer('))
        (licenses / 'build.json').write_text(json.dumps({
            'version': args.version, 'target': 'Debian 13 (trixie)',
            'vendors_sha256': manifests,
            'patches': ['soapy_power: binary numpy.fromstring replaced by numpy.frombuffer'],
        }, indent=2) + '\n')

        bootstrap = """#!/usr/bin/python3 -I
import os
import sys
sys.dont_write_bytecode = True
root = '/usr/lib/qspectrumanalyzer'
sys.path[:0] = [root, root + '/vendor']
os.environ['QT_PREFERRED_BINDING'] = 'PyQt5'
os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt5'
for key in ('PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
    os.environ.pop(key, None)
os.environ['PATH'] = root + '/bin:' + os.environ.get('PATH', '/usr/bin:/bin')
"""
        launch = stage / 'usr/bin/qspectrumanalyzer'
        launch.parent.mkdir(parents=True)
        launch.write_text(bootstrap + 'from qspectrumanalyzer.__main__ import main\nmain()\n')
        launch.chmod(0o755)
        backend = app / 'bin/soapy_power'
        backend.parent.mkdir()
        backend.write_text(bootstrap + 'from soapypower.__main__ import main\nmain()\n')
        backend.chmod(0o755)
        desktop = stage / 'usr/share/applications'
        desktop.mkdir(parents=True)
        shutil.copy(ROOT / 'qspectrumanalyzer.desktop', desktop)
        icons = stage / 'usr/share/icons/hicolor/scalable/apps'
        icons.mkdir(parents=True)
        shutil.copy(ROOT / 'qspectrumanalyzer.svg', icons)
        control = stage / 'DEBIAN'
        control.mkdir()
        size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) // 1024 + 1
        (control / 'control').write_text(
            'Package: qspectrumanalyzer\n'
            f'Version: {args.version}\n'
            'Section: hamradio\nPriority: optional\nArchitecture: all\n'
            'Maintainer: QSpectrumAnalyzer local build <local@localhost>\n'
            'Depends: python3 (>= 3.13), python3 (<< 3.14), python3-numpy, python3-scipy, '
            'python3-pyqt5, python3-pyqt5.qtsvg, python3-pyqtgraph, python3-soapysdr\n'
            'Recommends: soapysdr-tools, soapysdr0.8-module-hackrf, soapysdr0.8-module-rtlsdr, '
            'hackrf, rtl-sdr\n'
            f'Installed-Size: {size}\n'
            'Description: SDR spectrum analyzer with recording, snapshots and mobile server\n'
            ' Custom desktop application with CSV recording, spectrum and waterfall\n'
            ' snapshots, offline analysis and authenticated mobile remote controls.\n'
            ' Includes a private soapy_power backend compatible with NumPy 2.\n'
        )
        (control / 'md5sums').write_text(''.join(
            '{}  {}\n'.format(hashlib.md5(p.read_bytes()).hexdigest(), p.relative_to(stage))
            for p in sorted(stage.rglob('*')) if p.is_file() and control not in p.parents))
        package = output / f'qspectrumanalyzer_{args.version}_all.deb'
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(package)], check=True)
        (output / (package.name + '.sha256')).write_text(
            hashlib.sha256(package.read_bytes()).hexdigest() + '  ' + package.name + '\n')
        print(package)


if __name__ == '__main__':
    main()
