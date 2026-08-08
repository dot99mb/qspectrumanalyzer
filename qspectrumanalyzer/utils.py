import os
import shlex
import shutil
import sys
from pathlib import Path

import numpy as np

from qspectrumanalyzer.qt import QtGui


def split_executable(command):
    """Split an executable setting without corrupting Windows paths."""
    command = str(command or "").strip()
    if not command:
        return []
    if os.path.isfile(command):
        return [command]
    return shlex.split(command, posix=sys.platform != "win32")


def executable_available(command):
    """Return whether the first program in an executable setting is runnable."""
    cmdline = split_executable(command)
    if not cmdline:
        return False
    executable = cmdline[0].strip('"')
    return os.path.isfile(executable) or shutil.which(executable) is not None


def executable_matches_backend(command, backend):
    """Return whether a configured command appears to launch ``backend``."""
    expected = str(backend).lower().replace("-", "_")
    raw_name = Path(str(command or "").strip().strip('"')).stem.lower().replace("-", "_")
    if raw_name == expected:
        return True
    cmdline = split_executable(command)
    if not cmdline:
        return False
    name = Path(cmdline[0].strip('"')).stem.lower().replace("-", "_")
    return name == expected


def find_backend_executable(backend):
    """Find an SDR backend in PATH and common Windows installation folders."""
    executable_name = backend + (".exe" if sys.platform == "win32" else "")
    found = shutil.which(executable_name) or shutil.which(backend)
    if found:
        return os.path.normpath(found)

    if sys.platform != "win32":
        return None

    application_dir = Path(sys.executable).resolve().parent
    launch_dir = Path(sys.argv[0]).resolve().parent
    bundle_dir = Path(getattr(sys, "_MEIPASS", application_dir))
    candidates = [
        application_dir / executable_name,
        application_dir / "tools" / "rtl-sdr" / executable_name,
        launch_dir / executable_name,
        launch_dir / "tools" / "rtl-sdr" / executable_name,
        bundle_dir / executable_name,
        bundle_dir / "tools" / "rtl-sdr" / executable_name,
    ]
    for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(environment_name)
        if base:
            candidates.extend([
                Path(base) / "PothosSDR" / "bin" / executable_name,
                Path(base) / "rtl-sdr" / executable_name,
            ])

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def smooth(x, window_len=11, window='hanning'):
    """Smooth 1D signal using specified window with given size"""
    x = np.array(x)
    if window_len < 3:
        return x

    if x.size < window_len:
        raise ValueError("Input data length must be greater than window size")

    if window not in ['rectangular', 'hanning', 'hamming', 'bartlett', 'blackman']:
        raise ValueError("Window must be 'rectangular', 'hanning', 'hamming', 'bartlett' or 'blackman'")

    if window == 'rectangular':
        # Moving average
        w = np.ones(window_len, 'd')
    else:
        w = getattr(np, window)(window_len)

    s = np.r_[2 * x[0] - x[window_len:1:-1], x, 2 * x[-1] - x[-1:-window_len:-1]]
    y = np.convolve(w / w.sum(), s, mode='same')

    return y[window_len - 1:-window_len + 1]


def str_to_color(color_string):
    """Create QColor from comma sepparated RGBA string"""
    return QtGui.QColor(*[int(c.strip()) for c in color_string.split(',')])


def color_to_str(color):
    """Create comma separated RGBA string from QColor"""
    return ", ".join([str(color.red()), str(color.green()), str(color.blue()), str(color.alpha())])


def human_time(seconds):
    """Format time in seconds to human readable form (e.g. 1 h 2 min 3 s)"""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)

    if h > 0:
        timestr = '{:.0f} h {:.0f} min {:.0f} s'.format(h, m, s)
    elif m > 0:
        timestr = '{:.0f} min {:.0f} s'.format(m, s)
    else:
        timestr = '{:.0f} s'.format(s)

    return timestr
