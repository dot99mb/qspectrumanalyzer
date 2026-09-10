import tempfile
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
        self.window.data_storage.wait()
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def capture(self):
        self.window.capture_snapshot()
        return self.window.snapshotWidget.selected_path()

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
        self.plot.curve_average.setData(self.x, self.y + 5)
        np.testing.assert_array_equal(self.plot.snapshot_curves[0].getData()[1], self.y)
        panel.displayCheckBox.setChecked(False)
        self.assertEqual(self.plot.snapshot_curves, [])
        self.capture()
        panel.displayCheckBox.setChecked(True)
        np.testing.assert_array_equal(self.plot.snapshot_curves[0].getData()[1], self.y + 5)
        panel.table.selectRow(1)
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

    def test_name_edit_persists_without_changing_data_or_image(self):
        path = self.capture()
        image = path.with_suffix('.png').read_bytes()
        panel = self.window.snapshotWidget
        panel.table.item(0, 0).setText('  Эфир вечером  ')
        snapshot = read_snapshot(path)
        self.assertEqual(snapshot['name'], 'Эфир вечером')
        np.testing.assert_array_equal(snapshot['curves'][0]['x'], self.x)
        np.testing.assert_array_equal(snapshot['curves'][0]['y'], self.y)
        self.assertEqual(path.with_suffix('.png').read_bytes(), image)
        panel.refresh_list()
        self.assertEqual(panel.table.item(0, 0).text(), 'Эфир вечером')
        panel.displayCheckBox.setChecked(True)
        self.assertEqual(self.plot.snapshot_curves[0].name(), 'Эфир вечером: Average')
        panel.table.item(0, 0).setText('')
        self.assertEqual(read_snapshot(path)['name'], '')

    def test_failed_rename_keeps_original_snapshot_and_table_name(self):
        path = self.capture()
        before = path.read_bytes()
        panel = self.window.snapshotWidget
        with patch('qspectrumanalyzer.snapshots.os.replace', side_effect=OSError('read only')), \
                patch.object(panel, 'error') as error:
            panel.table.item(0, 0).setText('New name')
        error.assert_called_once()
        self.assertEqual(panel.table.item(0, 0).text(), '')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.directory.glob('.snapshot-*')))

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
        self.plot.display_snapshot({'curves': [dict(name='Average', x=x, y=y,
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
        path = self.capture()
        saved = read_snapshot(path)['curves'][0]
        np.testing.assert_array_equal(saved['x'], x)
        np.testing.assert_array_equal(saved['y'], y)

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
