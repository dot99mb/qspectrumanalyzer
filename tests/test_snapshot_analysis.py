import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from Qt import QtCore, QtGui, QtWidgets
from qspectrumanalyzer.snapshot_analysis import (
    compare_peaks, ComparisonModel, SnapshotAnalysisWindow, load_snapshot, cursor_value)
from qspectrumanalyzer.snapshots import save_snapshot


def spectrum():
    x = np.arange(5.) * 1e6 + 100e6
    return {'kind': 'spectrum', 'view_range': [[100e6, 104e6], [-110, -30]], 'curves': [
        {'name': 'Max hold', 'x': x, 'y': np.array([-100, -50, -90, -60, -100.]), 'color': [255, 0, 0, 255]},
        {'name': 'Average', 'x': x, 'y': np.array([-100, -60, -95, -63, -100.]), 'color': [0, 255, 255, 255]},
    ]}


class SnapshotAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_comparison_threshold_ratio_and_missing_curves(self):
        rows = compare_peaks(spectrum(), -70)
        np.testing.assert_allclose(rows[:, :4], [[101, -50, -60, 10], [103, -60, -63, 3]])
        np.testing.assert_allclose(rows[:, 4], [10, 100 * 10**(-.3)])
        self.assertEqual(len(compare_peaks(spectrum(), -55)), 1)
        snap = spectrum()
        snap['curves'].pop()
        with self.assertRaisesRegex(ValueError, 'Max hold и Average'):
            compare_peaks(snap, -80)

    def test_interpolation_and_outside_average_range(self):
        snap = spectrum()
        snap['curves'][1]['x'] = np.array([100e6, 102e6])
        snap['curves'][1]['y'] = np.array([-70., -50.])
        rows = compare_peaks(snap, -80)
        self.assertEqual(rows[0, 2], -60)
        self.assertTrue(np.isnan(rows[1, 2]))
        self.assertTrue(np.isnan(rows[1, 4]))

    def test_cursor_interpolation_uses_only_neighbouring_samples(self):
        x = np.array([100., 102., 105.])
        for dtype in (np.float32, np.float64):
            y = np.array([-80, -70, -90], dtype=dtype)
            for frequency in (100., 101., 102., 104., 105.):
                self.assertAlmostEqual(cursor_value(x, y, frequency), np.interp(frequency, x, y))
            self.assertTrue(np.isnan(cursor_value(x, y, 99.)))
            self.assertTrue(np.isnan(cursor_value(x, y, 106.)))

    def test_numeric_sort_all_columns(self):
        model = ComparisonModel()
        original = compare_peaks(spectrum(), -80)
        for column in range(5):
            model.set_rows(original)
            model.sort(column, QtCore.Qt.AscendingOrder)
            self.assertLessEqual(model.rows[0, column], model.rows[1, column])
            model.sort(column, QtCore.Qt.DescendingOrder)
            self.assertGreaterEqual(model.rows[0, column], model.rows[1, column])

    def settle(self, window):
        deadline = time.monotonic() + 5
        while window.pending and time.monotonic() < deadline:
            self.app.processEvents()
            window.poll()
            time.sleep(.01)
        self.assertFalse(window.pending)

    def test_window_loads_pair_links_axis_rejects_mismatch_and_compares(self):
        with tempfile.TemporaryDirectory() as directory:
            snap = spectrum()
            image = QtGui.QImage(100, 100, QtGui.QImage.Format_RGB32)
            image.fill(QtCore.Qt.black)
            specpath = save_snapshot(directory, snap['curves'], snap['view_range'], image, name='Test spectrum')
            wf = {'frequencies': snap['curves'][0]['x'], 'history': np.array([[-100, -60, -90, -63, -100.]]),
                  'frequency_range': [100e6, 104e6], 'levels': [-100, -30],
                  'lut': np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 4))}
            wfpath = save_snapshot(directory, [], [[100e6, 104e6], [-1, 0]], image, waterfall=wf)
            window = SnapshotAnalysisWindow()
            window.directory.setText(directory)
            window.refresh()
            window.show()
            self.settle(window)
            self.assertEqual(window.files.rowCount(), 2)
            def select(path):
                for row in range(window.files.rowCount()):
                    if window.files.item(row, 0).data(QtCore.Qt.UserRole)[0] == path:
                        window.files.selectRow(row)
                self.settle(window)
            select(specpath)
            self.assertEqual(window.model.rowCount(), 2)
            for item in window.plot.listDataItems():
                self.assertEqual(item.curve.cacheMode(), QtWidgets.QGraphicsItem.DeviceCoordinateCache)
            select(wfpath)
            self.assertTrue(window.image.isVisible())
            point = window.plot.vb.mapViewToScene(QtCore.QPointF(101e6, -50))
            window.mouse_moved((point,))
            self.assertAlmostEqual(window.cursor_lines[0].value(), 101e6)
            self.assertAlmostEqual(window.cursor_lines[2].value(), 101e6)
            self.assertAlmostEqual(window.cursor_lines[1].value(), -50)
            point = window.waterfall_plot.vb.mapViewToScene(QtCore.QPointF(103e6, -.5))
            window.mouse_moved((point,))
            self.assertAlmostEqual(window.cursor_lines[0].value(), 103e6)
            self.assertAlmostEqual(window.cursor_lines[2].value(), 103e6)
            self.assertAlmostEqual(window.cursor_lines[1].value(), -60)
            self.assertAlmostEqual(window.cursor_lines[3].value(), -.5)
            self.assertIn('f=103.000000 MHz, P=-60.000 dB', window.spectrum_coordinates.text)
            self.assertIn('f=103.000000 MHz, проход=-0.50', window.waterfall_coordinates.text)
            class Click:
                accepted = False
                def button(self):
                    return QtCore.Qt.LeftButton
                def scenePos(self):
                    return window.plot.vb.mapViewToScene(QtCore.QPointF(101e6, -50))
                def accept(self):
                    self.accepted = True
            event = Click()
            window.mouse_clicked(event)
            self.assertTrue(event.accepted)
            self.assertAlmostEqual(window.cursor_lines[2].value(), 101e6)
            window.plot.setXRange(101e6, 102e6, padding=0)
            self.app.processEvents()
            np.testing.assert_allclose(window.plot.viewRange()[0], window.waterfall_plot.viewRange()[0])
            window.waterfall_plot.setXRange(102e6, 103e6, padding=0)
            self.app.processEvents()
            np.testing.assert_allclose(window.plot.viewRange()[0], window.waterfall_plot.viewRange()[0])
            window.zoom_mode.setCurrentIndex(1)
            self.assertEqual(window.plot.getViewBox().state['mouseEnabled'], [False, True])
            self.assertEqual(window.waterfall_plot.getViewBox().state['mouseEnabled'], [True, False])
            window.fit_view()
            np.testing.assert_allclose(window.plot.viewRange()[0], [100e6, 104e6])
            window.resize(1600, 1000)
            self.app.processEvents()
            self.assertGreater(window.graphics.height(), window.height() * .8)
            self.assertLess(window.viewer_status.height(), 60)
            window.waterfall['frequency_range'] = [200e6, 204e6]
            window.display_waterfall()
            self.assertFalse(window.image.isVisible())
            self.assertIn('не совпадают', window.viewer_status.text())
            self.assertFalse(window.cursor_lines[2].isVisible())
            window.clear_spectrum_plot()
            self.assertIn(window.cursor_lines[0], window.plot.items)
            self.assertFalse(window.cursor_lines[0].isVisible())
            window.close()
            self.app.processEvents()

    def test_waterfall_is_reduced_before_display(self):
        with tempfile.TemporaryDirectory() as directory:
            image = QtGui.QImage(10, 10, QtGui.QImage.Format_RGB32)
            image.fill(QtCore.Qt.black)
            wf = {'frequencies': np.arange(5000.), 'history': np.zeros((3, 5000)),
                  'frequency_range': [0, 5000], 'levels': [-100, 0],
                  'lut': np.zeros((256, 4), dtype=np.uint8)}
            path = save_snapshot(directory, [], [[0, 5000], [-3, 0]], image, waterfall=wf)
            loaded = load_snapshot(path)
            self.assertEqual(loaded['pixels'].shape, (3, 1600))
            self.assertNotIn('history', loaded)
