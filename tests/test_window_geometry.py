import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from Qt import QtCore, QtWidgets

from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow


class WindowGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                                self.temp.name)
        self.app.setOrganizationName('WindowGeometryTests')
        self.app.setApplicationName('Geometry')
        self.available = QtCore.QRect(0, 0, 800, 600)
        screen = SimpleNamespace(availableGeometry=lambda: self.available)
        self.screen_patch = patch.object(QSpectrumAnalyzerMainWindow, 'screen', return_value=screen)
        self.screen_patch.start()
        self.windows = []

    def settle(self):
        for _ in range(8):
            self.app.processEvents()

    def window(self):
        window = QSpectrumAnalyzerMainWindow()
        self.windows.append(window)
        self.settle()
        return window

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.settle()
        self.screen_patch.stop()
        self.temp.cleanup()

    def assert_fits(self, window):
        self.assertTrue(self.available.contains(window.frameGeometry()),
                        (self.available.getRect(), window.frameGeometry().getRect()))
        self.assertLessEqual(window.minimumSizeHint().height(), self.available.height())
        self.assertGreater(window.mainPlotLayout.height(), 0)
        self.assertGreater(window.waterfallPlotLayout.height(), 0)

    def test_first_launch_on_small_screens_and_scaled_desktop(self):
        for width, height in ((800, 600), (1024, 600), (640, 480)):
            with self.subTest(size=(width, height)):
                QtCore.QSettings().clear()
                self.available = QtCore.QRect(0, 0, width, height)
                window = self.window()
                self.assert_fits(window)
                scroll = window.settingsDockWidget.widget()
                self.assertIsInstance(scroll, QtWidgets.QScrollArea)
                self.assertIs(scroll.widget(), window.settingsDockWidgetContents)
                window.settingsDockWidget.raise_()
                self.settle()
                scroll.ensureWidgetVisible(window.subtractBaselineCheckBox)
                self.settle()
                point = window.subtractBaselineCheckBox.mapTo(scroll.viewport(), QtCore.QPoint(0, 0))
                self.assertTrue(scroll.viewport().rect().intersects(
                    QtCore.QRect(point, window.subtractBaselineCheckBox.size())))
                window.close()

    def test_large_layout_is_preserved_then_restored_on_small_screen(self):
        self.available = QtCore.QRect(0, 0, 1920, 1600)
        original = self.window()
        self.assertIs(original.settingsDockWidget.widget(), original.settingsDockWidgetContents)
        original.resize(1500, 1400)
        original.close()
        self.available = QtCore.QRect(100, 50, 800, 600)
        restored = self.window()
        self.assert_fits(restored)
        self.assertIn(restored.snapshotsDockWidget,
                      restored.tabifiedDockWidgets(restored.settingsDockWidget))

    def test_maximized_window_and_saved_maximized_state(self):
        window = self.window()
        window.showMaximized()
        self.settle()
        window.ensure_window_visible()
        self.settle()
        self.assertTrue(window.isMaximized())
        self.assert_fits(window)
        window.close()
        restored = self.window()
        self.assertTrue(restored.isMaximized())
        self.assert_fits(restored)

    def test_hidden_and_floating_panels_survive_compaction(self):
        self.available = QtCore.QRect(0, 0, 1920, 1600)
        window = self.window()
        window.recordingDockWidget.hide()
        window.peaksDockWidget.setFloating(True)
        self.available = QtCore.QRect(0, 0, 640, 480)
        window.ensure_window_visible()
        self.settle()
        self.assert_fits(window)
        self.assertTrue(window.recordingDockWidget.isHidden())
        self.assertTrue(window.newSignalsDock.isHidden())
        self.assertTrue(window.peaksDockWidget.isFloating())
        window.show_all_dock_panels()
        self.settle()
        self.assert_fits(window)


if __name__ == '__main__':
    unittest.main()
