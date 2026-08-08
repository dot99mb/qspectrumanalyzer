import numpy as np

from qspectrumanalyzer.data import HistoryBuffer
from qspectrumanalyzer.backends.rtl_power import PowerThread as RtlPowerThread
from qspectrumanalyzer.data import DataStorage
from qspectrumanalyzer.peaks import find_peak_frequencies, find_signal_bands
from qspectrumanalyzer.qt import QtCore
from qspectrumanalyzer.settings import ensure_backend_settings_consistent
from qspectrumanalyzer.utils import (
    executable_available,
    executable_matches_backend,
    smooth,
    split_executable,
)
from qspectrumanalyzer.windows_theme import THEME_DARK, THEME_LIGHT, resolve_theme


def test_peak_detection_is_sorted_and_filtered():
    x = np.arange(7, dtype=float)
    y = np.array([-90, -30, -80, -20, -70, -40, -90], dtype=float)

    assert find_peak_frequencies(x, y, min_power=-35) == [(3.0, -20.0), (1.0, -30.0)]


def test_signal_bands_are_grouped_and_sorted():
    x = np.arange(6, dtype=float) * 10
    y = np.array([-90, -40, -35, -90, -30, -90], dtype=float)

    assert find_signal_bands(x, y, band_floor=-50) == [
        (35.0, 45.0, 40.0, -30.0),
        (5.0, 25.0, 20.0, -35.0),
    ]


def test_history_buffer_retains_newest_rows():
    history = HistoryBuffer(data_size=2, max_history_size=2)
    history.append([1, 2])
    history.append([3, 4])
    history.append([5, 6])

    np.testing.assert_array_equal(history.get_buffer(), [[3, 4], [5, 6]])


def test_rtl_power_uses_reported_value_count_for_axis():
    storage = DataStorage(max_history_size=2)
    storage.set_frequency_range(87e6, 108e6)
    thread = RtlPowerThread(storage)
    thread.setup(87, 108, 10, single_shot=True)

    thread.parse_output("2026-01-01, 12:00:00, 87000000, 108000000, 10000, 1, -40, -41, -42\n")
    storage.wait()

    assert len(storage.x) == 3
    assert len(storage.y) == 3


def test_smoothing_keeps_input_length():
    values = np.linspace(-10, 10, 31)
    result = smooth(values, window_len=5, window="hanning")

    assert len(result) == len(values)
    assert np.isfinite(result).all()


def test_windows_executable_path_with_spaces(tmp_path):
    executable = tmp_path / "SDR Tools" / "backend.exe"
    executable.parent.mkdir()
    executable.touch()

    assert split_executable(str(executable)) == [str(executable)]
    assert executable_available(str(executable))


def test_backend_command_matching():
    assert executable_matches_backend(r"C:\SDR Tools\rtl_power.exe", "rtl_power")
    assert not executable_matches_backend("soapy_power", "rtl_power")


def test_mixed_legacy_backend_settings_are_repaired(tmp_path):
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("backend", "rtl_power")
    settings.setValue("executable", "soapy_power")
    settings.setValue("params", "--even --remove-dc")

    ensure_backend_settings_consistent(settings)

    assert settings.value("executable") == "rtl_power"
    assert settings.value("params") == ""
    assert settings.value("device") == "0"
    assert settings.value("sample_rate", type=float) == 0
    assert settings.value("settings_backend") == "rtl_power"


def test_explicit_theme_resolution_does_not_depend_on_registry():
    assert resolve_theme(THEME_LIGHT) == (THEME_LIGHT, False)
    assert resolve_theme(THEME_DARK) == (THEME_DARK, True)
