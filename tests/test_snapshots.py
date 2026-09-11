import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from Qt import QtCore, QtGui, QtWidgets
from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow
from qspectrumanalyzer.snapshots import read_snapshot, save_snapshot, SnapshotWidget


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, self.temp.name)
        self.app.setOrganizationName('SnapshotTests')
        self.app.setApplicationName('Snapshots')
        self.directory = Path(self.temp.name) / 'captures'
        self.directory.mkdir()
        QtCore.QSettings().setValue('snapshots/directory', str(self.directory))
        self.window = QSpectrumAnalyzerMainWindow()
        self.window.show()
        self.window.snapshotsDockWidget.raise_()
        self.app.processEvents()
        self.plot = self.window.spectrumPlotWidget
        self.x = np.arange(10) * 1e6 + 100e6
        self.y = np.arange(10.) - 90
        self.plot.curve.hide()
        self.plot.curve_average.show()
        self.plot.curve_average.setData(self.x, self.y)
        self.plot.plot.setRange(xRange=(100e6, 110e6), yRange=(-100, -70), padding=0)

    def tearDown(self):
        self.window.snapshotWidget.cancel_loading()
        self.wait_for_load()
        self.window.data_storage.wait()
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def capture(self):
        self.window.capture_snapshot()
        self.wait_for_capture()
        self.wait_for_load()
        return self.window.snapshotWidget.selected_path()

    def wait_for_load(self):
        deadline = time.monotonic() + 10
        while (self.window.snapshotWidget.load_thread is not None or self.window.snapshotWidget.rename_thread is not None) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.snapshotWidget.load_thread)
        self.assertIsNone(self.window.snapshotWidget.rename_thread)

    def wait_for_capture(self):
        deadline = time.monotonic() + 10
        while self.window.snapshot_save_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.snapshot_save_thread)

    def prepare_waterfall(self):
        w = self.window
        w.actionWaterfall.setChecked(True)
        w.startFreqSpinBox.setValue(100)
        w.stopFreqSpinBox.setValue(110)
        w.fit_frequency_range()
        w.data_storage.data_updated.disconnect(w.update_data)
        w.data_storage.set_frequency_range(100e6, 110e6)
        for offset in range(3):
            w.data_storage.update(dict(x=np.linspace(100e6, 110e6, 20),
                                       y=np.arange(20.) - 90 + offset, timestamp=time.time()))
            w.data_storage.wait()
            self.app.processEvents()
        w.snapshotWidget.typeComboBox.setCurrentIndex(1)

    def test_waterfall_roundtrip_and_linked_zoom(self):
        self.prepare_waterfall()
        waterfall = self.window.waterfallPlotWidget
        history = waterfall.waterfallImg.image.T.copy()
        path = self.capture()
        snapshot = read_snapshot(path)
        self.assertEqual(snapshot['kind'], 'waterfall')
        np.testing.assert_array_equal(snapshot['history'], history)
        self.assertEqual(snapshot['frequency_range'], [100e6, 110e6])
        self.assertFalse(QtGui.QImage(str(path.with_suffix('.png'))).isNull())
        waterfall.plot.setXRange(102e6, 104e6, padding=0)
        self.window.snapshotWidget.displayCheckBox.setChecked(True)
        self.wait_for_load()
        self.app.processEvents()
        np.testing.assert_allclose(waterfall.snapshot_plot.viewRange()[0], [102e6, 104e6], atol=1)
        waterfall.plot.setXRange(103e6, 105e6, padding=0)
        self.app.processEvents()
        np.testing.assert_allclose(waterfall.snapshot_plot.viewRange()[0], [103e6, 105e6], atol=1)
        np.testing.assert_array_equal(waterfall.snapshot_image.image.T,
                                      np.clip((history - snapshot['levels'][0]) * 255 /
                                              (snapshot['levels'][1] - snapshot['levels'][0]), 0, 255).astype(np.uint8))
        waterfall.snapshot_plot.setXRange(104e6, 106e6, padding=0)
        self.app.processEvents()
        np.testing.assert_allclose(waterfall.plot.viewRange()[0], [104e6, 106e6], atol=1)
        self.window.data_storage.update(dict(x=np.linspace(100e6, 110e6, 20),
                                            y=np.full(20, -50.), timestamp=time.time()))
        self.window.data_storage.wait()
        self.app.processEvents()
        np.testing.assert_array_equal(waterfall.snapshot_image.image.T,
                                      np.clip((history - snapshot['levels'][0]) * 255 /
                                              (snapshot['levels'][1] - snapshot['levels'][0]), 0, 255).astype(np.uint8))
        self.window.snapshotWidget.table.item(0, 0).setText('Водопад')
        self.wait_for_load()
        self.assertEqual(read_snapshot(path)['name'], 'Водопад')
        self.window.snapshotWidget.displayCheckBox.setChecked(False)
        self.assertIsNone(waterfall.snapshot_plot)
        self.assertIsNone(waterfall.snapshot_image)

    def test_waterfall_rejects_mismatched_range_and_hides_on_range_change(self):
        self.prepare_waterfall()
        self.capture()
        w = self.window
        w.stopFreqSpinBox.setValue(111)
        with patch.object(w.snapshotWidget, 'error') as error:
            w.snapshotWidget.displayCheckBox.setChecked(True)
            self.wait_for_load()
        error.assert_called_once()
        self.assertIsNone(w.waterfallPlotWidget.snapshot_plot)
        self.assertFalse(w.snapshotWidget.displayCheckBox.isChecked())
        w.stopFreqSpinBox.setValue(110)
        w.snapshotWidget.displayCheckBox.setChecked(True)
        self.wait_for_load()
        self.assertIsNotNone(w.waterfallPlotWidget.snapshot_plot)
        with patch.object(w.snapshotWidget, 'error') as error:
            w.startFreqSpinBox.setValue(101)
        error.assert_called_once()
        self.assertIsNone(w.waterfallPlotWidget.snapshot_plot)

    def test_waterfall_disabled_capture_rejected_and_display_released(self):
        self.prepare_waterfall()
        self.capture()
        w = self.window
        w.snapshotWidget.displayCheckBox.setChecked(True)
        self.wait_for_load()
        w.actionWaterfall.setChecked(False)
        self.assertIsNone(w.waterfallPlotWidget.snapshot_image)
        self.assertFalse(w.snapshotWidget.displayCheckBox.isChecked())
        with patch.object(w.snapshotWidget, 'error') as error:
            self.capture()
        error.assert_called_once()

    def test_capture_roundtrip_preview_and_directory_persistence(self):
        path = self.capture()
        snapshot = read_snapshot(path)
        self.assertEqual([c['name'] for c in snapshot['curves']], ['Average'])
        np.testing.assert_array_equal(snapshot['curves'][0]['x'], self.x)
        np.testing.assert_array_equal(snapshot['curves'][0]['y'], self.y)
        self.assertFalse(QtGui.QImage(str(path.with_suffix('.png'))).isNull())
        other = self.capture()
        self.assertNotEqual(path, other)
        panel = self.window.snapshotWidget
        self.assertEqual(panel.table.rowCount(), 2)
        panel.table.cellEntered.emit(1, 0)
        self.assertFalse(panel.previewLabel.pixmap().isNull())
        self.assertEqual(panel.preview_path, path.with_suffix('.png'))
        reloaded = SnapshotWidget()
        self.assertEqual(reloaded.table.rowCount(), 2)
        self.assertEqual(reloaded.directoryEdit.text(), str(self.directory))
        reloaded.close()

    def test_overlay_survives_live_changes_switches_and_clears(self):
        self.capture()
        panel = self.window.snapshotWidget
        panel.displayCheckBox.setChecked(True)
        self.wait_for_load()
        self.plot.curve_average.setData(self.x, self.y + 5)
        np.testing.assert_array_equal(self.plot.snapshot_curves[0].getData()[1], self.y)
        panel.displayCheckBox.setChecked(False)
        self.assertEqual(self.plot.snapshot_curves, [])
        self.capture()
        panel.displayCheckBox.setChecked(True)
        self.wait_for_load()
        np.testing.assert_array_equal(self.plot.snapshot_curves[0].getData()[1], self.y + 5)
        panel.table.selectRow(1)
        self.wait_for_load()
        self.assertEqual(len(self.plot.snapshot_curves), 1)
        np.testing.assert_array_equal(self.plot.snapshot_curves[0].getData()[1], self.y)
        panel.directoryEdit.setText(str(self.directory / 'missing'))
        panel.directory_changed()
        self.assertEqual(self.plot.snapshot_curves, [])

    def test_failed_save_leaves_no_partial_pair(self):
        with patch('qspectrumanalyzer.snapshots.np.savez_compressed', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                save_snapshot(self.directory, self.plot.snapshot_data(), self.plot.plot.viewRange(),
                              self.window.mainPlotLayout.grab())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_hidden_waterfall_snapshot_selection_does_not_load_or_warn(self):
        self.prepare_waterfall()
        self.capture()
        w = self.window
        w.actionWaterfall.setChecked(False)
        with patch('qspectrumanalyzer.snapshots.read_snapshot') as read, \
                patch.object(w.snapshotWidget, 'error') as error:
            w.snapshotWidget.displayCheckBox.setChecked(True)
            w.snapshotWidget.display_selected()
            self.app.processEvents()
        read.assert_not_called()
        error.assert_not_called()
        self.assertIsNone(w.snapshotWidget.load_thread)
        self.assertIsNone(w.waterfallPlotWidget.snapshot_plot)

    def test_spectrum_and_waterfall_histories_are_independent(self):
        spectrum_path = self.capture()
        self.prepare_waterfall()
        self.capture()
        w = self.window
        panel = w.snapshotWidget
        panel.displayCheckBox.setChecked(True)
        self.wait_for_load()
        waterfall_plot = w.waterfallPlotWidget.snapshot_plot
        for row in range(panel.table.rowCount()):
            if Path(panel.table.item(row, 0).data(QtCore.Qt.UserRole)) == spectrum_path:
                panel.table.selectRow(row)
                break
        self.wait_for_load()
        self.assertIs(w.waterfallPlotWidget.snapshot_plot, waterfall_plot)
        self.assertIsNotNone(self.plot.snapshot_plot)
        self.assertIsNot(self.plot.snapshot_plot, self.plot.plot)
        self.assertNotIn(self.plot.snapshot_curves[0], self.plot.plot.items)
        live_range = self.plot.plot.viewRange()
        panel.typeComboBox.setCurrentIndex(0)
        self.capture()
        self.assertIs(w.waterfallPlotWidget.snapshot_plot, waterfall_plot)
        np.testing.assert_allclose(self.plot.plot.viewRange(), live_range)
        spectrum_plot = self.plot.snapshot_plot
        panel.typeComboBox.setCurrentIndex(1)
        self.capture()
        self.assertIs(self.plot.snapshot_plot, spectrum_plot)
        self.assertIsNot(w.waterfallPlotWidget.snapshot_plot, waterfall_plot)
        w.actionWaterfall.setChecked(False)
        self.assertIs(self.plot.snapshot_plot, spectrum_plot)
        self.assertIsNone(w.waterfallPlotWidget.snapshot_plot)
        panel.displayCheckBox.setChecked(False)
        self.assertIsNone(self.plot.snapshot_plot)

    def test_double_click_applies_range_only_after_confirmation(self):
        self.capture()
        w = self.window
        before = (w.startFreqSpinBox.value(), w.stopFreqSpinBox.value())
        with patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.No), \
                patch.object(w, 'stop') as stop:
            w.snapshotWidget.table.cellDoubleClicked.emit(0, 0)
        stop.assert_not_called()
        self.assertEqual((w.startFreqSpinBox.value(), w.stopFreqSpinBox.value()), before)
        def confirm(*args):
            self.assertEqual((w.startFreqSpinBox.value(), w.stopFreqSpinBox.value()), before)
            return QtWidgets.QMessageBox.Yes
        with patch.object(QtWidgets.QMessageBox, 'question', side_effect=confirm), \
                patch.object(w, 'stop') as stop:
            w.snapshotWidget.table.cellDoubleClicked.emit(0, 2)
        stop.assert_called_once()
        self.assertEqual((w.startFreqSpinBox.value(), w.stopFreqSpinBox.value()), (100., 110.))
        np.testing.assert_allclose(w.spectrumPlotWidget.plot.viewRange()[0], [100e6, 110e6], atol=1)
        self.assertFalse(w.power_thread.alive)

    def test_waterfall_double_click_uses_full_range_not_zoomed_view(self):
        self.prepare_waterfall()
        w = self.window
        w.waterfallPlotWidget.plot.setXRange(102e6, 104e6, padding=0)
        self.capture()
        w.startFreqSpinBox.setValue(101)
        with patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.Yes):
            w.snapshotWidget.table.cellDoubleClicked.emit(0, 0)
        self.assertEqual((w.startFreqSpinBox.value(), w.stopFreqSpinBox.value()), (100., 110.))

    def test_loading_is_responsive_and_latest_selection_wins(self):
        self.capture()
        self.plot.curve_average.setData(self.x, self.y + 10)
        self.capture()
        entered, release = threading.Event(), threading.Event()
        def delayed_read(*args, **kwargs):
            entered.set()
            release.wait(5)
            return read_snapshot(*args, **kwargs)
        panel = self.window.snapshotWidget
        with patch('qspectrumanalyzer.snapshots.read_snapshot', side_effect=delayed_read):
            try:
                panel.displayCheckBox.setChecked(True)
                self.assertTrue(entered.wait(1))
                ticks = []
                QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
                self.app.processEvents()
                self.assertEqual(ticks, [True])
                panel.table.selectRow(1)
                self.assertEqual(self.plot.snapshot_curves, [])
            finally:
                release.set()
                self.wait_for_load()
        np.testing.assert_array_equal(self.plot.snapshot_curves[0].getOriginalDataset()[1], self.y)

    def test_cancel_and_close_during_loading_discards_result(self):
        self.capture()
        release = threading.Event()
        def delayed_read(*args, **kwargs):
            release.wait(5)
            return read_snapshot(*args, **kwargs)
        panel = self.window.snapshotWidget
        with patch('qspectrumanalyzer.snapshots.read_snapshot', side_effect=delayed_read):
            try:
                panel.displayCheckBox.setChecked(True)
                panel.displayCheckBox.setChecked(False)
                self.window.close()
                self.assertTrue(self.window.isVisible())
            finally:
                release.set()
                self.wait_for_load()
        self.assertEqual(self.plot.snapshot_curves, [])
        self.assertFalse(self.window.isVisible())

    def test_background_load_error_is_reported(self):
        self.capture()
        panel = self.window.snapshotWidget
        with patch('qspectrumanalyzer.snapshots.read_snapshot', side_effect=ValueError('broken snapshot')), \
                patch.object(panel, 'error') as error:
            panel.displayCheckBox.setChecked(True)
            self.wait_for_load()
        error.assert_called_once_with('broken snapshot')
        self.assertEqual(self.plot.snapshot_curves, [])

    def test_slow_save_keeps_event_loop_responsive_and_rejects_duplicate_capture(self):
        entered, release = threading.Event(), threading.Event()
        real_save = save_snapshot
        def delayed_save(*args):
            entered.set()
            release.wait(5)
            return real_save(*args)
        with patch('qspectrumanalyzer.snapshots.save_snapshot', side_effect=delayed_save) as save:
            try:
                self.window.capture_snapshot()
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.window.snapshotWidget.captureButton.isEnabled())
                ticks = []
                QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
                self.app.processEvents()
                self.assertEqual(ticks, [True])
                self.window.capture_snapshot()
                self.assertEqual(save.call_count, 1)
                self.plot.curve_average.setData(self.x, self.y + 10)
            finally:
                release.set()
                self.wait_for_capture()
        path = self.window.snapshotWidget.selected_path()
        np.testing.assert_array_equal(read_snapshot(path)['curves'][0]['y'], self.y)
        self.assertTrue(self.window.snapshotWidget.captureButton.isEnabled())

    def test_save_failure_reenables_capture_and_keeps_no_partial_pair(self):
        with patch('qspectrumanalyzer.snapshots.np.savez_compressed', side_effect=OSError('disk full')), \
                patch.object(self.window.snapshotWidget, 'error') as error:
            self.capture()
        error.assert_called_once_with('disk full')
        self.assertTrue(self.window.snapshotWidget.captureButton.isEnabled())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_close_waits_for_background_save_without_blocking(self):
        release = threading.Event()
        def delayed_save(*args):
            release.wait(5)
            return save_snapshot(*args)
        with patch('qspectrumanalyzer.snapshots.save_snapshot', side_effect=delayed_save):
            try:
                self.window.capture_snapshot()
                self.window.close()
                self.assertTrue(self.window.isVisible())
                self.assertTrue(self.window.snapshot_close_pending)
            finally:
                release.set()
                self.wait_for_capture()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(len(list(self.directory.glob('*.npz'))), 1)

    def test_missing_backend_does_not_start_worker_or_clear_graph(self):
        QtCore.QSettings().setValue('executable', '/missing/backend-executable')
        with patch.object(QtWidgets.QMessageBox, 'warning') as warning, \
                patch.object(self.window.power_thread, 'start') as start:
            self.window.start()
        warning.assert_called_once()
        start.assert_not_called()
        np.testing.assert_array_equal(self.plot.curve_average.getData()[1], self.y)

    def test_name_edit_persists_without_changing_data_or_image(self):
        path = self.capture()
        image = path.with_suffix('.png').read_bytes()
        panel = self.window.snapshotWidget
        panel.table.item(0, 0).setText('  Эфир вечером  ')
        self.wait_for_load()
        snapshot = read_snapshot(path)
        self.assertEqual(snapshot['name'], 'Эфир вечером')
        np.testing.assert_array_equal(snapshot['curves'][0]['x'], self.x)
        np.testing.assert_array_equal(snapshot['curves'][0]['y'], self.y)
        self.assertEqual(path.with_suffix('.png').read_bytes(), image)
        panel.refresh_list()
        self.assertEqual(panel.table.item(0, 0).text(), 'Эфир вечером')
        panel.displayCheckBox.setChecked(True)
        self.wait_for_load()
        self.assertEqual(self.plot.snapshot_curves[0].name(), 'Эфир вечером: Average')
        panel.table.item(0, 0).setText('')
        self.wait_for_load()
        self.assertEqual(read_snapshot(path)['name'], '')

    def test_failed_rename_keeps_original_snapshot_and_table_name(self):
        path = self.capture()
        before = path.read_bytes()
        panel = self.window.snapshotWidget
        with patch('qspectrumanalyzer.snapshots.os.replace', side_effect=OSError('read only')), \
                patch.object(panel, 'error') as error:
            panel.table.item(0, 0).setText('New name')
            self.wait_for_load()
        error.assert_called_once()
        self.assertEqual(panel.table.item(0, 0).text(), '')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.directory.glob('.snapshot-*')))

    def test_rename_stays_responsive_preserves_display_and_queues_latest_name(self):
        from qspectrumanalyzer.snapshots import rename_snapshot
        path = self.capture()
        panel = self.window.snapshotWidget
        panel.displayCheckBox.setChecked(True)
        self.wait_for_load()
        curve = self.plot.snapshot_curves[0]
        entered, release = threading.Event(), threading.Event()
        def delayed_rename(*args):
            entered.set()
            release.wait(5)
            return rename_snapshot(*args)
        with patch('qspectrumanalyzer.snapshots.rename_snapshot', side_effect=delayed_rename), \
                patch.object(panel, 'display_selected') as reload_snapshot:
            try:
                panel.table.item(0, 0).setText('First')
                self.assertTrue(entered.wait(1))
                ticks = []
                QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
                self.app.processEvents()
                self.assertEqual(ticks, [True])
                panel.table.item(0, 0).setText('Latest')
                self.assertIs(self.plot.snapshot_curves[0], curve)
            finally:
                release.set()
                self.wait_for_load()
            reload_snapshot.assert_not_called()
        self.assertEqual(read_snapshot(path)['name'], 'Latest')
        self.assertIs(self.plot.snapshot_curves[0], curve)
        self.assertEqual(curve.name(), 'Latest: Average')

    def test_startup_migrates_layout_without_snapshot_dock(self):
        w = self.window
        dock = w.snapshotsDockWidget
        legacy = QtWidgets.QMainWindow()
        peaks = QtWidgets.QDockWidget('Peaks', legacy)
        peaks.setObjectName('peaksDockWidget')
        legacy.addDockWidget(QtCore.Qt.LeftDockWidgetArea, peaks)
        old_state = legacy.saveState()
        legacy.close()
        QtCore.QSettings().setValue('window_state', old_state)
        w.load_settings()
        self.app.processEvents()
        self.assertIn(dock, w.tabifiedDockWidgets(w.peaksDockWidget))
        self.assertFalse(w.snapshotWidget.displayCheckBox.isChecked())
        self.assertTrue(w.isVisible())

    def test_startup_preserves_layout_that_includes_snapshot_dock(self):
        w = self.window
        w.addDockWidget(QtCore.Qt.LeftDockWidgetArea, w.snapshotsDockWidget)
        QtCore.QSettings().setValue('window_state', w.saveState())
        w.load_settings()
        self.app.processEvents()
        self.assertEqual(w.dockWidgetArea(w.snapshotsDockWidget), QtCore.Qt.LeftDockWidgetArea)

    def test_large_snapshot_reduces_display_preserving_peaks_and_saved_data(self):
        x = np.linspace(400e6, 5499e6, 510000)
        y = np.full(x.size, -90.)
        y[55001] = -40.
        y[55002] = -110.
        self.plot.curve_average.clear()
        self.plot.plot.setRange(xRange=(x[0], x[-1]), yRange=(-120, -30), padding=0)
        self.plot.display_snapshot({'view_range': [[x[0], x[-1]], [-120, -30]], 'curves': [dict(name='Average', x=x, y=y,
                                                  color=[0, 255, 255, 255])]})
        self.app.processEvents()
        curve = self.plot.snapshot_curves[0]
        display_x, display_y = curve.getData()
        self.assertLess(len(display_x), len(x) // 10)
        self.assertEqual(display_y.max(), -40.)
        self.assertEqual(display_y.min(), -110.)
        self.plot.plot.setXRange(x[54950], x[55050], padding=0)
        self.app.processEvents()
        self.assertIn(x[55001], curve.getData()[0])
        np.testing.assert_array_equal(curve.getOriginalDataset()[1], y)
        self.plot.curve_average.setData(self.x, self.y)
        path = self.capture()
        saved = read_snapshot(path)['curves'][0]
        np.testing.assert_array_equal(saved['x'], self.x)
        np.testing.assert_array_equal(saved['y'], self.y)

    def test_corrupt_files_skipped_and_empty_plot_rejected(self):
        (self.directory / 'spectrum_broken.npz').write_bytes(b'PK\x03\x04broken')
        self.window.snapshotWidget.refresh_list()
        self.assertIn('1 invalid', self.window.snapshotWidget.statusLabel.text())
        self.plot.curve_average.clear()
        with patch.object(self.window.snapshotWidget, 'error') as error:
            self.window.capture_snapshot()
        error.assert_called_once()
        self.assertEqual(len(list(self.directory.iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
