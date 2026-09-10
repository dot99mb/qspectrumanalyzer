import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from Qt import QtCore, QtWidgets

from qspectrumanalyzer.analysis import (
    read_recording, RecordingAnalysisWindow, RecordingTableModel, RecordingData, round_frequencies, signal_markers,
)
from qspectrumanalyzer.recording import CsvRecorder


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        recorder = CsvRecorder()
        recorder.start(self.directory.name, -60, ';')
        for second, y in [(1, [-50, -70]), (2, [-80, -90]), (3, [-40, -55])]:
            recorder.write_frame(([100e6, 101e6], y, 1700000000 + second,
                                  recorder.started_at + second, (99e6, 102e6)))
        recorder.stop()
        self.path = recorder.path

    def test_metadata_and_period_filters(self):
        data = read_recording(self.path)
        self.assertFalse(data.legacy)
        np.testing.assert_array_equal(data.elapsed, [1, 2, 3])
        indices, signals = data.select(2, 3, 99e6, 102e6, -60)
        np.testing.assert_array_equal(indices, [1, 2])
        peaks = data.band_series(indices, signals, 100e6, 100e6)
        self.assertTrue(np.isnan(peaks[0]))
        self.assertEqual(peaks[1], -40)
        indices, signals = data.select(0, 4, 100.5e6, 102e6, -50)
        self.assertEqual(len(signals), 0)
        indices, signals = data.select(0, 4, 99e6, 102e6, -100,
                                       (1700000002, 1700000002))
        np.testing.assert_array_equal(indices, [1])
        self.assertEqual(len(signals), 0)

    def test_legacy_and_custom_separator(self):
        path = Path(self.directory.name) / 'legacy.csv'
        with path.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream, delimiter='|')
            writer.writerow(['frequency_hz', 'power_db', 'system_time', 'elapsed_ms'])
            writer.writerow([100e6, -50, '2026-09-10T12:00:00+03:00', 0])
            writer.writerow([101e6, -55, '2026-09-10T12:00:00+03:00', 0])
            writer.writerow([100e6, -45, '2026-09-10T12:00:10+03:00', 10000])
        data = read_recording(path)
        self.assertTrue(data.legacy)
        self.assertEqual(len(data.sweeps), 2)
        self.assertEqual(data.sweeps[0]['hits'], 2)
        self.assertEqual(len(read_recording(path, '|').signals), 3)
        model = RecordingTableModel()
        indices, signals = data.select(0, 10, 0, 200e6, -100)
        model.set_records(data, indices, signals)
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.data(model.index(1, 4)), '101.000000')
        self.assertEqual(model.data(model.index(1, 6)), '101000000.0')
        self.assertEqual(model.data(model.index(0, 0)), 'signal')
        with self.assertRaises(ValueError):
            read_recording(path, ';')

    def test_truncated_file_and_cancellation(self):
        text = self.path.read_text()
        self.path.write_text('\n'.join(text.splitlines()[:-1]) + '\n')
        with self.assertRaisesRegex(ValueError, 'Incomplete sweep'):
            read_recording(self.path)
        with self.assertRaises(InterruptedError):
            read_recording(self.path, cancelled=lambda: True)

    def test_bad_numeric_and_unknown_sweep(self):
        text = self.path.read_text()
        self.path.write_text(text.replace('100000000.0;-50.0', 'NaN;-50.0'))
        with self.assertRaisesRegex(ValueError, 'CSV line'):
            read_recording(self.path)
        self.path.write_text(text.replace(';signal;1;', ';signal;999;'))
        with self.assertRaisesRegex(ValueError, 'unknown sweep'):
            read_recording(self.path)

    def test_window_async_loading_filters_and_render(self):
        window = RecordingAnalysisWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_file(str(self.path))
        deadline = time.monotonic() + 5
        while (window.data is None or window.loader.isRunning()) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertIsNotNone(window.data)
        self.assertEqual(window.tableModel.rowCount(), 6)
        self.assertEqual(window.tabs.count(), 2)
        self.assertEqual(window.tabs.widget(0), window.fileTab)
        self.assertEqual(window.tableModel.data(window.tableModel.index(1, 4)), '100.000000')
        self.assertEqual(window.tableModel.data(window.tableModel.index(1, 5)), '-50.000')
        window.bandCenter.setValue(101)
        window.bandWidth.setValue(100)
        window.refresh()
        y = window.selected_peaks
        self.assertTrue(np.isnan(y[0]))
        self.assertEqual(y[2], -55)
        window.period.setRegion((2, 3))
        window.period_changed()
        self.assertEqual(window.startSpin.value(), 2)
        self.assertEqual(window.tableModel.rowCount(), 4)
        window.minimumSpin.setValue(0)
        window.refresh()
        self.assertTrue(np.isnan(window.selected_peaks).all())
        self.assertEqual(len(window.mapScatter.points()), 0)
        self.assertFalse(window.grab().isNull())
        self.assertTrue(window.openButton.isEnabled())

    def test_zero_hit_recording_has_known_time_and_range(self):
        recorder = CsvRecorder()
        recorder.start(self.directory.name, -60, ',')
        recorder.write_frame(([100e6], [-70], time.time(), recorder.started_at + 1,
                              (99e6, 101e6)))
        recorder.stop()
        data = read_recording(recorder.path)
        self.assertEqual(data.signals.shape, (0, 3))
        self.assertEqual(data.sweeps[0]['hits'], 0)
        self.assertEqual(data.sweeps[0]['start'], 99e6)
        window = RecordingAnalysisWindow()
        self.addCleanup(window.close)
        window.data = data
        window.reset_filters()
        self.assertEqual(window.tableModel.rowCount(), 1)
        self.assertEqual(window.tableModel.data(window.tableModel.index(0, 4)), '—')
        self.assertEqual(window.tableModel.data(window.tableModel.index(0, 12)), '0')

    def test_main_window_analysis_action_reuses_window(self):
        from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow
        with tempfile.TemporaryDirectory() as config:
            QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
            QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, config)
            window = QSpectrumAnalyzerMainWindow()
            self.addCleanup(window.close)
            self.assertIn(window.actionAnalyzeRecording, window.menu_File.actions())
            window.actionAnalyzeRecording.trigger()
            first = window.analysis_window
            self.assertTrue(first.isVisible())
            first.close()
            window.actionAnalyzeRecording.trigger()
            self.assertIs(window.analysis_window, first)
            self.assertTrue(first.isVisible())
            window.close()
            self.assertFalse(first.isVisible())

    def test_file_table_keeps_multiple_frequencies_and_has_no_row_cap(self):
        from qspectrumanalyzer.analysis import RecordingData
        original = read_recording(self.path)
        model = RecordingTableModel()
        indices, signals = original.select(0, 4, 0, 200e6, -100)
        model.set_records(original, indices, signals)
        self.assertEqual(model.rowCount(), 6)
        self.assertEqual([model.data(model.index(i, 0)) for i in range(6)],
                         ['sweep', 'signal', 'sweep', 'sweep', 'signal', 'signal'])
        self.assertEqual(model.data(model.index(4, 4)), '100.000000')
        self.assertEqual(model.data(model.index(5, 4)), '101.000000')
        self.assertEqual(model.data(model.index(5, 5)), '-55.000')
        many = RecordingData(original.sweeps[:1], [(0, 100e6 + i, -50) for i in range(1500)])
        model.set_records(many, np.array([0]), many.signals)
        self.assertEqual(model.rowCount(), 1501)
        self.assertEqual(model.data(model.index(1500, 6)), '100001499.0')

    def test_rounding_methods_decimal_boundaries_and_units(self):
        values = [100122500., 100123500., 100124000.]
        np.testing.assert_array_equal(round_frequencies(values, 3, 'nearest'),
                                      [100123000., 100124000., 100124000.])
        np.testing.assert_array_equal(round_frequencies(values, 3, 'even'),
                                      [100122000., 100124000., 100124000.])
        np.testing.assert_array_equal(round_frequencies(values, 3, 'down'),
                                      [100122000., 100123000., 100124000.])
        np.testing.assert_array_equal(round_frequencies(values, 3, 'up'),
                                      [100123000., 100124000., 100124000.])
        np.testing.assert_array_equal(round_frequencies([100500000.], 0, 'nearest'), [101000000.])
        np.testing.assert_array_equal(round_frequencies([100000000.5], 6, 'nearest'), [100000001.])
        self.assertEqual(round_frequencies([], 3, 'nearest').size, 0)

    def test_marker_sizes_and_dense_map_preserve_strongest_actual_hit(self):
        times = np.array([1., 2., 3.])
        frequencies = np.array([100e6, 101e6, 102e6])
        powers = np.array([-75., -73., -71.])
        x, f, p, sizes = signal_markers(times, frequencies, powers, (-75, -71))
        np.testing.assert_array_equal(sizes, [4, 10, 16])
        _, _, _, filtered_sizes = signal_markers(times[1:2], frequencies[1:2], powers[1:2], (-75, -71))
        self.assertEqual(filtered_sizes[0], 10)
        _, _, _, equal_sizes = signal_markers(times, frequencies, powers * 0 - 70, (-70, -70))
        np.testing.assert_array_equal(equal_sizes, [10, 10, 10])
        powers = np.linspace(-90, -40, 40000)
        times = np.ones(40000) * 2.5
        frequencies = np.ones(40000) * 101e6
        x, f, p, sizes = signal_markers(times, frequencies, powers, (-90, -40))
        np.testing.assert_array_equal(x, [2.5])
        np.testing.assert_array_equal(f, [101e6])
        np.testing.assert_array_equal(p, [-40])
        np.testing.assert_array_equal(sizes, [16])

    def test_rounding_preview_apply_collision_and_restore(self):
        data = read_recording(self.path)
        data.signals = np.array([[0, 100123400., -50.], [0, 100123490., -40.],
                                 [2, 100149000., -45.]])
        original = data.signals.copy()
        window = RecordingAnalysisWindow()
        self.addCleanup(window.close)
        window.data = data
        window.reset_filters()
        previous_points = window.mapScatter.data.copy()
        window.roundingCheck.setChecked(True)
        self.assertEqual(window.tableModel.data(window.tableModel.index(1, 4)), '100.123')
        self.assertEqual(window.tableModel.data(window.tableModel.index(1, 6)), '100123000.0')
        self.assertEqual(window.tableModel.rowCount(), 6)
        self.assertIsNone(window.graph_data)
        np.testing.assert_array_equal(window.mapScatter.data["x"], previous_points["x"])
        np.testing.assert_array_equal(window.mapScatter.data["y"], previous_points["y"])
        np.testing.assert_array_equal(window.mapScatter.data["size"], previous_points["size"])
        self.assertEqual(window.selected_peaks[0], -50)
        window.refresh()  # Refreshing filters must not apply pending rounding.
        self.assertIsNone(window.graph_data)
        window.applyRoundingButton.click()
        self.assertEqual(window.graph_data.signals[0, 1], 100123000.)
        self.assertEqual(window.selected_peaks[0], -40)
        window.roundingDecimals.setValue(2)
        self.assertEqual(window.graph_data.signals[2, 1], 100149000.)
        window.applyRoundingButton.click()
        self.assertEqual(window.graph_data.signals[2, 1], 100150000.)
        window.roundingDecimals.setValue(1)
        window.applyRoundingButton.click()
        # Recompute from source rather than rounding the previously rounded .15.
        self.assertEqual(window.graph_data.signals[2, 1], 100100000.)
        window.originalButton.click()
        self.assertFalse(window.roundingCheck.isChecked())
        self.assertIsNone(window.graph_data)
        np.testing.assert_array_equal(data.signals, original)
        self.assertEqual(window.tableModel.data(window.tableModel.index(1, 4)), '100.123400')

    def test_rounding_preview_does_not_hide_frequency_at_filter_edge(self):
        data = read_recording(self.path)
        data.signals = np.array([[0, 100123456., -50.]])
        window = RecordingAnalysisWindow()
        self.addCleanup(window.close)
        window.data = data
        window.reset_filters()
        window.lowSpin.setValue(100.123456)
        window.highSpin.setValue(100.123456)
        window.roundingCheck.setChecked(True)
        self.assertEqual(len(window.tableModel.signals), 1)
        window.applyRoundingButton.click()
        self.assertLessEqual(window.lowSpin.value(), 100.123)
        self.assertEqual(window.selected_peaks[0], -50)


if __name__ == '__main__':
    unittest.main()
