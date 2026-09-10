import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from Qt import QtCore, QtWidgets

from qspectrumanalyzer.data import DataStorage
from qspectrumanalyzer.recording import CsvRecorder, RecordingWidget


class RecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setOrganizationName("QSpectrumAnalyzerTests")
        cls.app.setApplicationName("Recording")
        cls.config = tempfile.TemporaryDirectory()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat,
                                QtCore.QSettings.UserScope, cls.config.name)

    @classmethod
    def tearDownClass(cls):
        cls.config.cleanup()

    def setUp(self):
        QtCore.QSettings().clear()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.recorder = CsvRecorder()
        self.addCleanup(self.recorder.stop)

    def read_rows(self, path, delimiter=";"):
        with open(path, newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream, delimiter=delimiter))

    def read_signals(self, path, delimiter=";"):
        return [row for row in self.read_rows(path, delimiter) if row["record_type"] == "signal"]

    def test_threshold_timestamp_and_flush(self):
        self.recorder.start(self.directory.name, -60, ";")
        frame = ([100, 200, 300, 400, 500], [-61, -60, -59, np.nan, np.inf],
                 1700000000.125, self.recorder.started_at + 1.25)
        self.recorder.write_frame(frame)
        # Visible on disk before Stop, strict > threshold, no non-finite data.
        rows = self.read_signals(self.recorder.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frequency_hz"], "300.0")
        self.assertEqual(rows[0]["power_db"], "-59.0")
        self.assertEqual(rows[0]["elapsed_ms"], "1250")
        self.assertIn(".125", rows[0]["system_time"])
        self.assertRegex(rows[0]["system_time"], r"[+-]\d\d:\d\d$")

    def test_sessions_ignore_old_frames_and_reset_clock(self):
        self.recorder.start(self.directory.name, -60, ",")
        old_frame = ([100], [-50], time.time(), self.recorder.started_at + .25)
        self.recorder.write_frame(old_frame)
        first = self.recorder.path
        self.recorder.stop()
        self.recorder.write_frame(old_frame)
        self.recorder.start(self.directory.name, -60, ",")
        second = self.recorder.path
        self.recorder.write_frame(([200], [-40], time.time(), self.recorder.started_at - 1))
        self.recorder.write_frame(([300], [-30], time.time(), self.recorder.started_at + .5))
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.read_signals(first, ",")), 1)
        rows = self.read_signals(second, ",")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["elapsed_ms"], "500")

    def test_invalid_configuration_and_empty_session(self):
        for separator in ["", "::", "\n", '"']:
            with self.assertRaises(ValueError):
                self.recorder.start(self.directory.name, -60, separator)
        with self.assertRaises(ValueError):
            self.recorder.start("", -60, ";")
        with self.assertRaises(ValueError):
            self.recorder.start(str(Path(self.directory.name) / "missing"), -60, ";")
        self.assertFalse(self.recorder.active)
        self.recorder.start(self.directory.name, -60, ";")
        self.assertTrue(self.recorder.path.exists())
        self.recorder.stop()
        self.assertEqual(self.read_signals(self.recorder.path), [])

    def test_sweep_metadata_without_hits_and_signal_links(self):
        self.recorder.start(self.directory.name, -60, ";")
        for offset, powers, bounds in [
                (1, [-70., -60.], (87e6, 108e6)),
                (2, [-50., -40.], (100e6, 120e6)),
                (3, [np.nan, np.inf], (100e6, 120e6))]:
            self.recorder.write_frame(([101e6, 102e6], powers, 1700000000 + offset,
                                       self.recorder.started_at + offset, bounds))
        rows = self.read_rows(self.recorder.path)
        self.assertEqual([r["record_type"] for r in rows],
                         ["sweep", "sweep", "signal", "signal", "sweep"])
        sweeps = [r for r in rows if r["record_type"] == "sweep"]
        self.assertEqual([r["sweep_id"] for r in sweeps], ["1", "2", "3"])
        self.assertEqual([r["hit_count"] for r in sweeps], ["0", "2", "0"])
        self.assertEqual([r["valid_bin_count"] for r in sweeps], ["2", "2", "0"])
        self.assertEqual([r["elapsed_ms"] for r in sweeps], ["1000", "2000", "3000"])
        self.assertEqual(float(sweeps[0]["start_frequency_hz"]), 87e6)
        self.assertEqual(float(sweeps[1]["start_frequency_hz"]), 100e6)
        self.assertEqual(float(sweeps[1]["stop_frequency_hz"]), 120e6)
        for sweep in sweeps:
            self.assertEqual(sweep["threshold_db"], "-60.0")
            self.assertEqual(sweep["bin_count"], "2")
            self.assertEqual(sweep["frequency_hz"], "")
        for signal in rows[2:4]:
            self.assertEqual(signal["sweep_id"], "2")
            self.assertEqual(signal["system_time"], sweeps[1]["system_time"])
            self.assertEqual(signal["elapsed_ms"], sweeps[1]["elapsed_ms"])
        self.assertEqual(self.recorder.sweeps, 3)
        self.assertEqual(self.recorder.rows, 2)
        self.recorder.stop()
        self.recorder.start(self.directory.name, -60, "|")
        self.recorder.write_frame(([100], [-70], time.time(), self.recorder.started_at + 1))
        restarted = self.read_rows(self.recorder.path, "|")
        self.assertEqual(restarted[0]["sweep_id"], "1")
        self.assertEqual(self.recorder.rows, 0)

    def test_empty_spectrum_and_stale_frames(self):
        self.recorder.start(self.directory.name, -60, ";")
        self.recorder.write_frame(([], [], time.time(), self.recorder.started_at - 1))
        self.assertEqual(self.read_rows(self.recorder.path), [])
        self.recorder.write_frame(([], [], time.time(), self.recorder.started_at + 1,
                                   (87e6, 108e6)))
        rows = self.read_rows(self.recorder.path)
        self.assertEqual(rows[0]["sweep_id"], "1")
        self.assertEqual(rows[0]["bin_count"], "0")
        self.assertEqual(rows[0]["hit_count"], "0")

    def test_configured_range_survives_worker_queue(self):
        storage = DataStorage()
        frames = []
        storage.set_frequency_range(87e6, 108e6)
        # Capture work instead of running it until settings have changed.
        with patch.object(storage, "start_task") as enqueue:
            storage.update({"x": [88e6, 100e6], "y": [-80., -90.]})
        captured = enqueue.call_args_list[1].args[1]
        storage.set_frequency_range(200e6, 300e6)
        storage.recording_frame_ready.connect(frames.append)
        storage.update_data(captured)
        storage.wait()
        self.assertEqual(frames[0][4], (87e6, 108e6))

    def test_panel_settings_and_lifecycle(self):
        widget = RecordingWidget()
        self.addCleanup(widget.close)
        widget.directoryEdit.setText(self.directory.name)
        widget.thresholdSpinBox.setValue(-42.5)
        widget.delimiterEdit.setText("|")
        widget.start_recording()
        self.addCleanup(widget.stop_recording)
        self.assertTrue(widget.recorder.active)
        self.assertFalse(widget.startButton.isEnabled())
        self.assertFalse(widget.directoryEdit.isEnabled())
        self.assertTrue(widget.stopButton.isEnabled())
        restored = RecordingWidget()
        self.addCleanup(restored.close)
        self.assertEqual(restored.directoryEdit.text(), self.directory.name)
        self.assertEqual(restored.thresholdSpinBox.value(), -42.5)
        self.assertEqual(restored.delimiterEdit.text(), "|")
        widget.stop_recording()
        self.assertTrue(widget.startButton.isEnabled())
        self.assertFalse(widget.stopButton.isEnabled())

    def test_write_error_stops_recording_and_reports_failure(self):
        widget = RecordingWidget()
        self.addCleanup(widget.close)
        widget.directoryEdit.setText(self.directory.name)
        widget.start_recording()
        with patch.object(widget.recorder, "write_frame", side_effect=OSError("Disk full")), \
                patch.object(QtWidgets.QMessageBox, "warning") as warning:
            widget.record_frame(([100], [-10], time.time(), time.monotonic()))
        warning.assert_called_once()
        self.assertFalse(widget.recorder.active)
        self.assertTrue(widget.startButton.isEnabled())
        self.assertIn("Disk full", widget.statusLabel.text())

    def test_storage_emits_distinct_frames_when_gui_is_delayed(self):
        storage = DataStorage()
        frames = []
        storage.recording_frame_ready.connect(frames.append, QtCore.Qt.QueuedConnection)
        self.recorder.start(self.directory.name, -60, ";")
        for y in [[-50., -90.], [-80., -40.]]:
            storage.update({"x": [100., 200.], "y": y, "timestamp": time.time()})
        storage.wait()
        self.app.processEvents()
        self.assertEqual(len(frames), 2)
        np.testing.assert_array_equal(frames[0][1], [-50, -90])
        np.testing.assert_array_equal(frames[1][1], [-80, -40])
        for frame in frames:
            self.recorder.write_frame(frame)
        rows = self.read_signals(self.recorder.path)
        self.assertEqual([r["frequency_hz"] for r in rows], ["100.0", "200.0"])

    def test_main_window_dock_menu_restore_and_close(self):
        from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow
        window = QSpectrumAnalyzerMainWindow()
        dock = window.recordingDockWidget
        self.assertIn(dock, window.dock_widgets)
        self.assertIn(dock.toggleViewAction(), window.menu_View.actions())
        dock.hide()
        window.show_all_dock_panels()
        self.assertFalse(dock.isHidden())
        widget = window.recordingWidget
        widget.directoryEdit.setText(self.directory.name)
        widget.start_recording()
        dock.hide()
        self.assertTrue(widget.recorder.active)
        window.close()
        self.assertFalse(widget.recorder.active)
        restored = QSpectrumAnalyzerMainWindow()
        self.assertTrue(restored.recordingDockWidget.isHidden())
        self.assertEqual(restored.recordingWidget.directoryEdit.text(), self.directory.name)
        restored.close()


if __name__ == "__main__":
    unittest.main()
