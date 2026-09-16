import time, sys, os

from Qt import QtCore
import numpy as np

from qspectrumanalyzer.utils import smooth
from qspectrumanalyzer.backends import soapy_power


class HistoryBuffer:
    """Fixed-size NumPy array ring buffer"""
    def __init__(self, data_size, max_history_size, dtype=float):
        self.data_size = data_size
        self.max_history_size = max_history_size
        self.history_size = 0
        self.counter = 0
        self.buffer = np.empty(shape=(max_history_size, data_size), dtype=dtype)
        self.times = np.full((max_history_size, 2), np.nan)
        self.timed_frame = (self.buffer[:0], self.times[:0])

    def append(self, data, wall_time=np.nan, monotonic_time=np.nan):
        """Append new data to ring buffer"""
        self.counter += 1
        if self.history_size < self.max_history_size:
            self.history_size += 1
        if self.max_history_size > 1:
            self.buffer = np.roll(self.buffer, -1, axis=0)
            self.times = np.roll(self.times, -1, axis=0)
        self.buffer[-1] = data
        self.times[-1] = (wall_time, monotonic_time)
        self.timed_frame = (self.buffer[-self.history_size:], self.times[-self.history_size:])

    def get_buffer(self):
        """Return buffer stripped to size of actual data"""
        if self.history_size < self.max_history_size:
            return self.buffer[-self.history_size:]
        else:
            return self.buffer

    def __getitem__(self, key):
        return self.buffer[key]


class TaskSignals(QtCore.QObject):
    """Task signals emitter"""
    result = QtCore.Signal(object)


class Task(QtCore.QRunnable):
    """Threaded task (run it with QThreadPool worker threads)"""
    def __init__(self, task, *args, **kwargs):
        super().__init__()
        self.task = task
        self.args = args
        self.kwargs = kwargs
        self.signals = TaskSignals()

    def run(self):
        """Run task in worker thread and emit signal with result"""
        #print('Running', self.task, 'in thread', QtCore.QThread.currentThreadId())
        result = self.task(*self.args, **self.kwargs)
        self.signals.result.emit(result)


class DataStorage(QtCore.QObject):
    """Data storage for spectrum measurements"""
    detection_updated = QtCore.Signal(object)
    history_updated = QtCore.Signal(object)
    history_resized = QtCore.Signal(object)
    data_updated = QtCore.Signal(object)
    recording_frame_ready = QtCore.Signal(object)
    history_recalculated = QtCore.Signal(object)
    data_recalculated = QtCore.Signal(object)
    average_updated = QtCore.Signal(object)
    baseline_updated = QtCore.Signal(object)
    peak_hold_max_updated = QtCore.Signal(object)
    peak_hold_min_updated = QtCore.Signal(object)

    def __init__(self, max_history_size=100, parent=None):
        super().__init__(parent)
        self.max_history_size = max_history_size
        self.emit_history_updates = True
        self.smooth = False
        self.smooth_length = 11
        self.smooth_window = "hanning"
        self.subtract_baseline = False
        self.prev_baseline = None
        self.baseline = None
        self.baseline_x = None
        self.frequency_start = None
        self.frequency_stop = None
        self.frequency_axis_warning_shown = False

        # Use only one worker thread because it is not faster
        # with more threads (and memory consumption is much higher)
        self.threadpool = QtCore.QThreadPool()
        self.threadpool.setMaxThreadCount(1)

        self.reset()

    def reset(self):
        """Reset all data"""
        self.wait()
        self.x = None
        self.history = None
        self.detector = None
        self.detection_last_emit = 0.
        self.detection_updated.emit(("Сканирование сброшено. Зафиксируйте фон заново.", [], False))
        self.reset_data()

    def reset_data(self):
        """Reset current data"""
        self.wait()
        self.y = None
        self.average_counter = 0
        self.average = None
        self.peak_hold_max = None
        self.peak_hold_min = None

    def start_task(self, fn, *args, **kwargs):
        """Run function asynchronously in worker thread"""
        task = Task(fn, *args, **kwargs)
        self.threadpool.start(task)

    def wait(self):
        """Wait for worker threads to complete all running tasks"""
        self.threadpool.waitForDone()

    def set_frequency_range(self, start_freq, stop_freq):
        """Set configured sweep range in Hz for validating backend x-axis data."""
        self.frequency_start = min(start_freq, stop_freq)
        self.frequency_stop = max(start_freq, stop_freq)
        self.frequency_axis_warning_shown = False

    def configure_history(self, size, emit_updates):
        """Serialize retention changes with acquisition/processing tasks."""
        self.start_task(self._configure_history, max(1, int(size)), bool(emit_updates))

    def _configure_history(self, size, emit_updates):
        self.emit_history_updates = emit_updates
        self.max_history_size = size
        if self.history is not None and self.history.max_history_size != size:
            old = self.history
            recent = old.get_buffer()[-size:]
            resized = HistoryBuffer(old.data_size, size)
            resized.history_size = len(recent)
            resized.counter = old.counter
            if len(recent):
                resized.buffer[-len(recent):] = recent
                resized.times[-len(recent):] = old.times[-old.history_size:][-size:]
                resized.timed_frame = (resized.buffer[-len(recent):], resized.times[-len(recent):])
            # Recalculation can leave the current spectrum referencing history.
            if self.y is not None and np.shares_memory(self.y, old.buffer):
                self.y = self.y.copy()
            self.history = resized
        self.history_resized.emit(self)

    def normalize_frequency_axis(self, data):
        """Build x-axis bin centers from the configured sweep range."""
        if self.frequency_start is None or self.frequency_stop is None:
            return data

        y = data.get("y")
        x = data.get("x")
        if x is None or y is None or len(x) != len(y) or len(x) < 2:
            return data

        x = np.asarray(x, dtype=float)
        configured_span = self.frequency_stop - self.frequency_start
        actual_span = x[-1] - x[0]
        if configured_span <= 0:
            return data

        bin_width = configured_span / len(x)
        data = data.copy()
        data["x"] = np.linspace(
            self.frequency_start + bin_width / 2,
            self.frequency_stop - bin_width / 2,
            len(x)
        )
        if not self.frequency_axis_warning_shown:
            print("Frequency axis rebuilt from configured range: x span={} Hz, configured span={} Hz".format(
                actual_span, configured_span
            ))
            self.frequency_axis_warning_shown = True

        return data

    def update(self, data):
        """Update data storage"""
        data = data.copy()
        data["recording_time"] = (time.time(), time.monotonic())
        data["recording_range"] = (self.frequency_start, self.frequency_stop)
        data = self.normalize_frequency_axis(data)

        if self.y is not None and len(data["y"]) != len(self.y):
            print("{:d} bins coming from backend, expected {:d}".format(len(data["y"]), len(self.y)))
            return

        self.average_counter += 1

        if self.x is None:
            self.x = data["x"]

        # Subtract baseline from data
        data["y"] = np.asarray(data["y"])
        if self.subtract_baseline and self.baseline is not None and len(data["y"]) == len(self.baseline):
            data["y"] -= self.baseline

        self.start_task(self.update_history, data.copy())
        self.start_task(self.update_data, data)

    def update_data(self, data):
        """Update main spectrum data (and possibly apply smoothing)"""
        if self.smooth:
            data["y"] = self.smooth_data(data["y"])

        self.y = data["y"]
        wall_time, monotonic_time = data["recording_time"]
        # GUI notifications otherwise carry this mutable storage object. CSV
        # needs a distinct snapshot of every sweep, even when GUI delivery lags.
        self.recording_frame_ready.emit((np.array(self.x, copy=True),
                                         self.y.copy(), wall_time, monotonic_time,
                                         data["recording_range"]))
        self.data_updated.emit(self)

        self.start_task(self.update_average, data)
        self.start_task(self.update_peak_hold_max, data)
        self.start_task(self.update_peak_hold_min, data)

    def configure_detection(self, config=None):
        self.start_task(self._configure_detection, config)

    def _configure_detection(self, config):
        self.detector = None
        if config is None:
            self.detection_updated.emit(("Анализ остановлен. Для продолжения зафиксируйте фон.", [], False))
            return
        try:
            from .signal_detection import SignalDetector
            if self.history is None or self.x is None:
                raise ValueError('Нет истории водопада. Запустите сканирование.')
            history = self.history.get_buffer()[-64:]
            self.detector = SignalDetector(self.x, history, **config)
            self.detection_updated.emit(("Фон зафиксирован по {} проходам. Ожидание новых сигналов.".format(len(history)), [], True))
        except Exception as error:
            self.detection_updated.emit((str(error), [], False))

    def update_history(self, data):
        """Update spectrum measurements history"""
        if self.history is None:
            self.history = HistoryBuffer(len(data["y"]), self.max_history_size)

        wall, mono = data["recording_time"]
        self.history.append(data["y"], wall, mono)
        if self.detector is not None:
            try:
                rows = self.detector.update(data["x"], data["y"], wall, mono)
                if mono - self.detection_last_emit >= .5:
                    self.detection_last_emit = mono
                    self.detection_updated.emit(("Фон зафиксирован. Подтверждённых событий: {}.".format(len(rows)), rows, True))
            except Exception as error:
                self.detector = None
                self.detection_updated.emit((str(error), [], False))
        if self.emit_history_updates:
            self.history_updated.emit(self)

    def update_average(self, data):
        """Update average data"""
        if self.average is None:
            self.average = data["y"].copy()
        else:
            self.average = np.average((self.average, data["y"]), axis=0, weights=(self.average_counter - 1, 1))
        self.average_updated.emit(self)

    def update_peak_hold_max(self, data):
        """Update max. peak hold data"""
        if self.peak_hold_max is None:
            self.peak_hold_max = data["y"].copy()
        else:
            self.peak_hold_max = np.maximum(self.peak_hold_max, data["y"])
            self.peak_hold_max_updated.emit(self)

    def update_peak_hold_min(self, data):
        """Update min. peak hold data"""
        if self.peak_hold_min is None:
            self.peak_hold_min = data["y"].copy()
        else:
            self.peak_hold_min = np.minimum(self.peak_hold_min, data["y"])
            self.peak_hold_min_updated.emit(self)

    def smooth_data(self, y):
        """Apply smoothing function to data"""
        return smooth(y, window_len=self.smooth_length, window=self.smooth_window)

    def set_smooth(self, toggle, length=11, window="hanning"):
        """Toggle smoothing and set smoothing params"""
        if toggle != self.smooth or length != self.smooth_length or window != self.smooth_window:
            self.smooth = toggle
            self.smooth_length = length
            self.smooth_window = window
            self.start_task(self.recalculate_data)

    def set_subtract_baseline(self, toggle, baseline_file=None):
        """Toggle baseline subtraction and set baseline"""
        baseline = None
        baseline_x = None

        # Load baseline from file (compute average if there are multiple PSD data in file)
        if baseline_file and os.path.isfile(baseline_file):
            average_counter = 0
            with open(baseline_file, 'rb') as f:
                for data in soapy_power.read_from_file(f):
                    average_counter += 1
                    if baseline is None:
                        baseline = data['y'].copy()
                        baseline_x = data['x'].copy()
                    else:
                        baseline = np.average((baseline, data['y']), axis=0, weights=(average_counter - 1, 1))

        # Don't subtract baseline if number of bins in baseline differs from number of bins in data
        if self.y is not None and baseline is not None and len(self.y) != len(baseline):
            print("Can't subtract baseline (expected {:d} bins, but baseline has {:d} bins)".format(
                len(self.y), len(baseline)
            ))
            #baseline = None

        if self.subtract_baseline:
            self.prev_baseline = self.baseline

        #if not np.array_equal(baseline, self.baseline):
        self.baseline = baseline
        self.baseline_x = baseline_x
        self.baseline_updated.emit(self)

        self.subtract_baseline = toggle
        self.start_task(self.recalculate_history)
        self.start_task(self.recalculate_data)

    def recalculate_history(self):
        """Recalculate spectrum measurements history"""
        if self.history is None:
            return

        if self.detector is not None:
            self._configure_detection(None)
        history = self.history.get_buffer()
        if self.prev_baseline is not None and len(history[-1]) == len(self.prev_baseline):
            history += self.prev_baseline
            self.prev_baseline = None
        if self.subtract_baseline and self.baseline is not None and len(history[-1]) == len(self.baseline):
            history -= self.baseline

        self.history_recalculated.emit(self)

    def recalculate_data(self):
        """Recalculate current data from history"""
        if self.history is None:
            return

        history = self.history.get_buffer()
        if self.smooth:
            self.y = self.smooth_data(history[-1])
            self.average_counter = 1
            self.average = self.y.copy()
            self.peak_hold_max = self.y.copy()
            self.peak_hold_min = self.y.copy()
            for y in history[:-1]:
                self.average_counter += 1
                y = self.smooth_data(y)
                self.average = np.average((self.average, y), axis=0, weights=(self.average_counter - 1, 1))
                self.peak_hold_max = np.maximum(self.peak_hold_max, y)
                self.peak_hold_min = np.minimum(self.peak_hold_min, y)
        else:
            self.y = history[-1]
            self.average_counter = self.history.history_size
            self.average = np.average(history, axis=0)
            self.peak_hold_max = history.max(axis=0)
            self.peak_hold_min = history.min(axis=0)

        self.data_recalculated.emit(self)
        #self.data_updated.emit({"x": self.x, "y": self.y})
        #self.average_updated.emit({"x": self.x, "y": self.average})
        #self.peak_hold_max_updated.emit({"x": self.x, "y": self.peak_hold_max})
        #self.peak_hold_min_updated.emit({"x": self.x, "y": self.peak_hold_min})


class Test:
    """Test data storage performance"""
    def __init__(self, data_size=100000, max_history_size=100):
        self.data_size = data_size
        self.data = {"x": np.arange(data_size),
                     "y": None}
        self.datastorage = DataStorage(max_history_size)

    def run_one(self):
        """Generate random data and update data storage"""
        self.data["y"] = np.random.normal(size=self.data_size)
        self.datastorage.update(self.data)

    def run(self, runs=1000):
        """Run performance test"""
        t = time.time()
        for i in range(runs):
            self.run_one()
        self.datastorage.wait()
        total_time = time.time() - t
        print("Total time:", total_time)
        print("FPS:", runs / total_time)


if __name__ == "__main__":
    test = Test(int(sys.argv[1]), int(sys.argv[2]))
    test.run(int(sys.argv[3]))
