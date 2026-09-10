import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from Qt import QtCore, QtWidgets
from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow


class WaterfallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.config = tempfile.TemporaryDirectory()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, self.config.name)
        self.app.setOrganizationName('QSpectrumAnalyzerWaterfallTests')
        self.app.setApplicationName('Waterfall')
        QtCore.QSettings().setValue('waterfall_history_size', 50)
        self.window = QSpectrumAnalyzerMainWindow()
        self.window.data_storage.data_updated.disconnect(self.window.update_data)
        self.settle()

    def tearDown(self):
        self.window.data_storage.wait()
        self.window.close()
        self.app.processEvents()
        self.config.cleanup()

    def settle(self):
        self.window.data_storage.wait()
        for _ in range(4):
            self.app.processEvents()

    def feed(self, offset=0):
        self.window.data_storage.update({'x': np.arange(16) * 1e6,
                                        'y': np.arange(16, dtype=float) - 90 + offset,
                                        'timestamp': time.time()})
        self.settle()

    def test_disable_frees_image_and_history_and_stops_processing(self):
        w = self.window
        for i in range(3):
            self.feed(i)
        self.assertTrue(hasattr(w.waterfallPlotWidget, 'waterfallImg'))
        self.assertEqual(w.data_storage.history.max_history_size, 50)
        w.actionWaterfall.setChecked(False)
        self.settle()
        self.assertTrue(w.waterfallPlotLayout.isHidden())
        self.assertTrue(w.levelsDockWidget.isHidden())
        self.assertFalse(hasattr(w.waterfallPlotWidget, 'waterfallImg'))
        self.assertEqual(w.data_storage.history.max_history_size, 1)
        with patch('qspectrumanalyzer.data.np.roll') as roll, \
                patch.object(w.waterfallPlotWidget, 'set_image_data') as image, \
                patch.object(w.waterfallPlotWidget.histogram, 'imageChanged') as histogram:
            self.feed(10)
            self.feed(20)
            roll.assert_not_called()
            image.assert_not_called()
            histogram.assert_not_called()
        np.testing.assert_array_equal(w.data_storage.y, np.arange(16) - 70)
        self.assertEqual(w.data_storage.history.history_size, 1)
        self.assertEqual(w.data_storage.average_counter, 5)
        w.show_all_dock_panels()
        w.ensure_plot_splitter_visible()
        self.assertTrue(w.waterfallPlotLayout.isHidden())
        self.assertTrue(w.levelsDockWidget.isHidden())
        w.actionWaterfall.setChecked(True)
        self.settle()
        self.feed(30)
        self.assertFalse(w.waterfallPlotLayout.isHidden())
        self.assertTrue(hasattr(w.waterfallPlotWidget, 'waterfallImg'))
        self.assertEqual(w.data_storage.history.max_history_size, 50)
        self.assertEqual(w.data_storage.history.history_size, 2)

    def test_persistence_retains_only_required_history_and_smoothing_works(self):
        w = self.window
        self.feed()
        w.actionWaterfall.setChecked(False)
        w.persistenceCheckBox.setChecked(True)
        self.settle()
        self.assertEqual(w.data_storage.max_history_size, 6)
        for i in range(8):
            self.feed(i)
        self.assertEqual(w.data_storage.history.history_size, 6)
        self.assertFalse(hasattr(w.waterfallPlotWidget, 'waterfallImg'))
        w.persistenceCheckBox.setChecked(False)
        self.settle()
        self.assertEqual(w.data_storage.history.max_history_size, 1)
        w.smoothCheckBox.setChecked(True)
        self.settle()
        self.assertEqual(w.data_storage.average_counter, 1)
        self.assertTrue(np.isfinite(w.data_storage.y).all())
        self.feed(10)
        self.assertEqual(w.data_storage.average_counter, 2)
        self.assertTrue(np.isfinite(w.data_storage.average).all())

    def test_disabled_state_survives_restart_and_backend_setup(self):
        w = self.window
        w.actionWaterfall.setChecked(False)
        self.settle()
        w.setup_power_thread()
        self.settle()
        self.assertEqual(w.data_storage.max_history_size, 1)
        self.assertFalse(w.data_storage.emit_history_updates)
        w.close()
        self.window = QSpectrumAnalyzerMainWindow()
        self.settle()
        self.assertFalse(self.window.actionWaterfall.isChecked())
        self.assertTrue(self.window.waterfallPlotLayout.isHidden())
        self.assertEqual(self.window.data_storage.max_history_size, 1)


if __name__ == '__main__':
    unittest.main()
