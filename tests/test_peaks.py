import csv
import tempfile
import time
import unittest
from unittest.mock import Mock

import numpy as np
from Qt import QtCore, QtWidgets
import pyqtgraph as pg
from qspectrumanalyzer.peaks import PeakListWidget
from qspectrumanalyzer.plot import SpectrumPlotWidget


class PeakPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.config = tempfile.TemporaryDirectory()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, self.config.name)
        self.app.setOrganizationName('PeakPanelTests')
        self.app.setApplicationName('Peaks')
        self.panel = PeakListWidget()
        self.layout = pg.GraphicsLayoutWidget()
        self.spectrum = SpectrumPlotWidget(self.layout)
        self.spectrum.peak_cursor_callback = self.panel.follow_frequency
        self.panel.peak_selected.connect(self.spectrum.set_cursor)
        self.x = np.arange(9) * 1000. + 100123400.
        self.y = np.array([-90., -50., -40., -55., -90., -90., -30., -90., -90.])
        self.panel.bandFloorSpinBox.setValue(-60)
        self.panel.update_peaks(self.x, self.y)

    def tearDown(self):
        self.panel.close()
        self.layout.close()
        self.app.processEvents()
        self.config.cleanup()

    def row_for(self, frequency):
        for row in range(self.panel.table.rowCount()):
            if self.panel.table.item(row, 0).data(QtCore.Qt.UserRole + 1)[0] == frequency:
                return row
        self.fail('Missing peak')

    def test_width_in_peak_and_band_modes_uses_original_edges(self):
        p = self.panel
        row = self.row_for(self.x[2])
        self.assertEqual(p.table.item(row, 3).text(), '3.000 kHz')
        p.signalBandsCheckBox.setChecked(True)
        p.update_peaks(self.x, self.y)
        self.assertEqual(p.table.columnCount(), 5)
        row = self.row_for(self.x[2])
        self.assertEqual(p.table.item(row, 4).text(), '3.000 kHz')
        p.roundingDecimalsSpinBox.setValue(2)
        p.roundingCheckBox.setChecked(True)
        row = self.row_for(self.x[2])
        self.assertEqual(p.table.item(row, 1).text(), '100.12–100.13 MHz')
        self.assertEqual(p.table.item(row, 4).text(), '3.000 kHz')
        p.bandFloorSpinBox.setValue(-20)
        p.signalBandsCheckBox.setChecked(False)
        p.update_peaks(self.x, self.y)
        self.assertEqual(p.table.item(0, 3).text(), '—')

    def test_click_sorted_rounded_row_moves_to_exact_peak_only_when_enabled(self):
        p = self.panel
        self.spectrum.set_cursor(1, 2)
        p.select_table_peak(0)
        self.assertEqual(self.spectrum.vLine.value(), 1)
        p.roundingCheckBox.setChecked(True)
        p.roundingModeComboBox.setCurrentIndex(p.roundingModeComboBox.findData('down'))
        p.followCursorCheckBox.setChecked(True)
        p.table.sortByColumn(1, QtCore.Qt.AscendingOrder)
        row = self.row_for(self.x[2])
        self.assertEqual(p.table.item(row, 1).text(), '100.125 MHz')
        p.table.cellClicked.emit(row, 1)
        self.assertEqual(self.spectrum.vLine.value(), self.x[2])
        self.assertEqual(self.spectrum.hLine.value(), -40)
        p.followCursorCheckBox.setChecked(False)
        p.table.cellClicked.emit(self.row_for(self.x[6]), 1)
        self.assertEqual(self.spectrum.vLine.value(), self.x[2])

    def test_reverse_cursor_selection_and_refresh_preserve_peak(self):
        p = self.panel
        p.followCursorCheckBox.setChecked(True)
        self.assertTrue(p.follow_frequency(self.x[6] + 100, -30, 200, 1))
        self.assertEqual(p.selected_peak()[0], self.x[6])
        self.assertEqual(self.spectrum.hLine.value(), -30)
        updated = self.y.copy()
        updated[6] = -35
        p.update_peaks(self.x, updated)
        self.assertEqual(p.selected_peak()[0], self.x[6])
        self.assertEqual(self.spectrum.hLine.value(), -35)
        self.layout.resize(700, 400)
        self.layout.show()
        self.spectrum.plot.setXRange(self.x[0], self.x[-1], padding=0)
        self.spectrum.plot.setYRange(-100, -20, padding=0)
        self.app.processEvents()
        pos = self.spectrum.plot.vb.mapViewToScene(QtCore.QPointF(self.x[2], -70))
        self.spectrum.mouse_moved((pos,))
        self.assertEqual(p.selected_peak()[0], self.x[6])
        self.assertAlmostEqual(self.spectrum.vLine.value(), self.x[2])
        self.assertAlmostEqual(self.spectrum.hLine.value(), -70)
        event = Mock()
        event.button.return_value = QtCore.Qt.LeftButton
        event.scenePos.return_value = pos
        self.spectrum.mouse_clicked(event)
        self.assertEqual(p.selected_peak()[0], self.x[6])
        self.assertAlmostEqual(self.spectrum.hLine.value(), -70)
        event.scenePos.return_value = self.spectrum.plot.vb.mapViewToScene(QtCore.QPointF(self.x[2], -40))
        self.spectrum.mouse_clicked(event)
        self.assertEqual(p.selected_peak()[0], self.x[2])
        self.assertEqual(self.spectrum.hLine.value(), -40)
        self.spectrum.mouse_moved((pos,))
        self.assertAlmostEqual(self.spectrum.hLine.value(), -70)
        p.clear()
        self.assertFalse(p.follow_frequency(self.x[2], -40, 100, 1))

    def test_click_hit_radius_is_in_pixels_and_empty_space_does_not_select_neighbor(self):
        p = self.panel
        p.followCursorCheckBox.setChecked(True)
        self.layout.resize(700, 400)
        self.layout.show()
        self.app.processEvents()
        event = Mock()
        event.button.return_value = QtCore.Qt.LeftButton
        for span in (10000, 1000000):
            self.spectrum.plot.setXRange(self.x[2] - span / 2, self.x[2] + span / 2, padding=0)
            self.spectrum.plot.setYRange(-100, -20, padding=0)
            self.app.processEvents()
            tip = self.spectrum.plot.vb.mapViewToScene(QtCore.QPointF(self.x[2], -40))
            p.table.clearSelection()
            p.table.setCurrentCell(-1, -1)
            self.spectrum.set_cursor(self.x[0], -90)
            event.scenePos.return_value = tip + QtCore.QPointF(0, 16)
            self.spectrum.mouse_clicked(event)
            self.assertIsNone(p.selected_peak())
            self.assertEqual(self.spectrum.hLine.value(), -90)
            event.scenePos.return_value = tip + QtCore.QPointF(2, 2)
            self.spectrum.mouse_clicked(event)
            self.assertEqual(p.selected_peak()[0], self.x[2])
            self.assertEqual(self.spectrum.hLine.value(), -40)

    def test_options_restore(self):
        self.panel.roundingCheckBox.setChecked(True)
        self.panel.roundingDecimalsSpinBox.setValue(4)
        self.panel.followCursorCheckBox.setChecked(True)
        restored = PeakListWidget()
        self.assertTrue(restored.roundingCheckBox.isChecked())
        self.assertEqual(restored.roundingDecimalsSpinBox.value(), 4)
        self.assertTrue(restored.followCursorCheckBox.isChecked())
        restored.close()

    def test_all_peaks_above_threshold_and_explicit_row_limit(self):
        x = np.arange(101) * 1000. + 100e6
        y = np.full(101, -90.)
        y[1::2] = np.linspace(-79, -30, 50)
        p = self.panel
        p.minPowerSpinBox.setValue(-80)
        p.bandFloorSpinBox.setValue(-80)
        p.update_peaks(x, y)
        self.assertEqual(p.total_found, 50)
        self.assertEqual(p.table.rowCount(), 50)
        self.assertIn('50 / 50', p.countLabel.text())
        p.limitSpinBox.setValue(20)
        self.assertEqual(p.table.rowCount(), 20)
        self.assertEqual(p.total_found, 50)
        self.assertIn('20 / 50', p.countLabel.text())
        displayed = [p.table.item(r, 0).data(QtCore.Qt.UserRole + 1)[1] for r in range(20)]
        self.assertEqual(min(displayed), -49)
        p.limitSpinBox.setValue(0)
        self.assertEqual(p.table.rowCount(), 50)
        p.signalBandsCheckBox.setChecked(True)
        p.update_peaks(x, y)
        self.assertEqual(p.table.rowCount(), 50)
        p.minPowerSpinBox.setValue(-40)
        p.update_peaks(x, y)
        self.assertEqual(p.table.rowCount(), 11)

    def test_double_click_sets_both_saved_triggers_and_live_csv_threshold(self):
        from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow
        window = QSpectrumAnalyzerMainWindow()
        try:
            window.show()
            window.spectrumPlotWidget.plot.setXRange(100e6, 110e6, padding=0)
            window.spectrumPlotWidget.plot.setYRange(-100, -20, padding=0)
            self.app.processEvents()
            plot = window.spectrumPlotWidget
            event = Mock()
            event.button.return_value = QtCore.Qt.LeftButton
            event.double.return_value = False
            event.scenePos.return_value = plot.plot.vb.mapViewToScene(QtCore.QPointF(105e6, -65.4))
            previous = window.recordingWidget.thresholdSpinBox.value()
            plot.mouse_clicked(event)
            self.assertEqual(window.recordingWidget.thresholdSpinBox.value(), previous)
            event.double.return_value = True
            plot.set_cursor(106e6, -30)  # The click position, not crosshair state, sets the level.
            plot.mouse_clicked(event)
            self.assertEqual(window.peakListWidget.minPowerLabel.text(), 'Trigger level:')
            self.assertEqual(window.peakListWidget.minPowerSpinBox.value(), -65.4)
            self.assertEqual(window.recordingWidget.thresholdSpinBox.value(), -65.4)
            with tempfile.TemporaryDirectory() as directory:
                recorder_widget = window.recordingWidget
                recorder_widget.directoryEdit.setText(directory)
                recorder_widget.start_recording()
                recorder = recorder_widget.recorder
                recorder_widget.record_frame(([105e6], [-60.], time.time(), time.monotonic(), (100e6, 110e6)))
                event.scenePos.return_value = plot.plot.vb.mapViewToScene(QtCore.QPointF(105e6, -55.2))
                plot.mouse_clicked(event)
                self.assertEqual(recorder.threshold, -55.2)
                recorder_widget.record_frame(([105e6], [-60.], time.time(), time.monotonic(), (100e6, 110e6)))
                recorder_widget.stop_recording()
                with recorder.path.open(newline='') as stream:
                    rows = list(csv.DictReader(stream, delimiter=';'))
                sweeps = [r for r in rows if r['record_type'] == 'sweep']
                self.assertEqual([r['threshold_db'] for r in sweeps], ['-65.4', '-55.2'])
                self.assertEqual([r['hit_count'] for r in sweeps], ['1', '0'])
            restored = PeakListWidget()
            self.assertEqual(restored.minPowerSpinBox.value(), -55.2)
            self.assertEqual(QtCore.QSettings().value('recording/threshold', type=float), -55.2)
            restored.close()
        finally:
            window.close()


if __name__ == '__main__':
    unittest.main()
