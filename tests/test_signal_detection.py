import tempfile
import unittest
import numpy as np
from Qt import QtGui
from qspectrumanalyzer.signal_detection import SignalDetector
from qspectrumanalyzer.data import HistoryBuffer
from qspectrumanalyzer.snapshots import save_snapshot, read_snapshot


class DetectionTests(unittest.TestCase):
    def detector(self):
        self.x = np.arange(20.) * 1000
        self.background = np.full((5, 20), -100.)
        self.background[:, 2] = -60
        return SignalDetector(self.x, self.background, seconds=4, tolerance=1000)

    def feed(self, detector, t, index=None):
        y = self.background[-1].copy()
        if index is not None:
            y[index] = -65
        return detector.update(self.x, y, 1000+t, t)

    def test_persistence_and_disappearance(self):
        d = self.detector()
        for t in range(4):
            self.assertEqual(self.feed(d, t, 10), [])
        rows = self.feed(d, 4, 10)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][10])
        self.assertEqual(rows[0][9], 100)
        for t in range(5, 11):
            rows = self.feed(d, t)
        self.assertFalse(rows[0][10])

    def test_existing_signal_and_drift_excluded(self):
        d = self.detector()
        for t in range(10):
            self.assertEqual(self.feed(d, t, 3), [])

    def test_transient_and_gap_not_persistent(self):
        d = self.detector()
        self.feed(d, 0, 10)
        for t in range(1, 10):
            self.assertEqual(self.feed(d, t), [])
        self.assertEqual(self.feed(d, 30, 10), [])

    def test_frequency_drift_same_event(self):
        d = self.detector()
        for t in range(10):
            rows = self.feed(d, t, 10+t%2)
        self.assertEqual(len(rows), 1)

    def test_timestamp_alignment_and_snapshot(self):
        history = HistoryBuffer(3, 3)
        for t in range(5):
            history.append(np.full(3, t), 1000+t, t)
        data, timestamps = history.timed_frame
        np.testing.assert_array_equal(data[:, 0], timestamps[:, 1])
        np.testing.assert_array_equal(timestamps[:, 1], [2, 3, 4])
        image = QtGui.QImage(2, 2, QtGui.QImage.Format_RGB32)
        image.fill(0)
        waterfall = dict(frequencies=np.arange(3.), history=data, timestamps=timestamps,
                         frequency_range=[0, 2], levels=[-100, 0],
                         lut=np.zeros((256, 3), dtype=np.uint8))
        with tempfile.TemporaryDirectory() as directory:
            path = save_snapshot(directory, [], [[0, 2], [-3, 0]], image, waterfall)
            np.testing.assert_array_equal(read_snapshot(path)['timestamps'], timestamps)
