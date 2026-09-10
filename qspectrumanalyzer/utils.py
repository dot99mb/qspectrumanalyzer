from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_FLOOR, ROUND_CEILING

import numpy as np

from Qt import QtGui


ROUNDING_MODES = {
    'nearest': ROUND_HALF_UP,
    'even': ROUND_HALF_EVEN,
    'down': ROUND_FLOOR,
    'up': ROUND_CEILING,
}


def round_frequencies(frequencies, decimals, mode):
    """Round Hz values to a decimal MHz grid, with explicit tie handling."""
    if decimals not in range(7) or mode not in ROUNDING_MODES:
        raise ValueError('Choose 0–6 MHz decimal places and a valid rounding mode.')
    step = Decimal(10) ** (6 - decimals)
    unique, inverse = np.unique(np.asarray(frequencies, dtype=float), return_inverse=True)
    # Round each distinct frequency once. Decimal avoids binary arithmetic
    # sending an exact decimal boundary or half-step to the wrong grid cell.
    rounded = np.array([float((Decimal(str(value)) / step).to_integral_value(
        rounding=ROUNDING_MODES[mode]) * step) for value in unique])
    return rounded[inverse]


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
