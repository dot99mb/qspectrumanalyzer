"""Threshold-triggered CSV recording and its dock panel controls."""

import csv
from datetime import datetime
from pathlib import Path
import time
import uuid

import numpy as np
from Qt import QtCore, QtWidgets


class CsvRecorder:
    """Write each qualifying frequency bin, using a monotonic session clock."""

    def __init__(self):
        self.file = None
        self.path = None
        self.rows = 0
        self.sweeps = 0

    @property
    def active(self):
        return self.file is not None

    def start(self, directory, threshold, delimiter):
        if self.active:
            raise ValueError("Recording is already running.")
        if len(delimiter) != 1 or delimiter in '\r\n\0"':
            raise ValueError('Use one separator character, excluding quotes and line breaks.')
        if not str(directory).strip():
            raise ValueError("Select an output directory.")
        directory = Path(directory).expanduser()
        if not directory.is_dir():
            raise ValueError("Select an existing output directory.")
        self.threshold = float(threshold)
        if not np.isfinite(self.threshold):
            raise ValueError("Trigger level must be finite.")
        name = "signals_{}_{}.csv".format(
            datetime.now().strftime("%Y%m%d_%H%M%S_%f"), uuid.uuid4().hex[:8]
        )
        self.path = directory / name
        self.file = self.path.open("x", encoding="utf-8", newline="")
        self.started_at = time.monotonic()
        self.rows = 0
        self.sweeps = 0
        try:
            self.writer = csv.writer(self.file, delimiter=delimiter)
            self.writer.writerow([
                "frequency_hz", "power_db", "system_time", "elapsed_ms",
                "record_type", "sweep_id", "start_frequency_hz", "stop_frequency_hz",
                "threshold_db", "bin_count", "valid_bin_count", "hit_count",
            ])
            self.file.flush()
        except Exception:
            self.stop()
            raise

    def write_frame(self, frame):
        if not self.active:
            return
        x, y, wall_time, monotonic_time = frame[:4]
        # Queued frames from before Start must never enter a new session.
        if monotonic_time < self.started_at:
            return
        x, y = np.asarray(x), np.asarray(y)
        if x.ndim != 1 or y.ndim != 1 or x.shape != y.shape:
            raise ValueError("Spectrum frequency and power arrays must have matching lengths.")
        valid = np.isfinite(x) & np.isfinite(y)
        selected = np.flatnonzero(valid & (y > self.threshold))
        # Use the configured range captured with the frame, not current GUI
        # controls (which may have changed while the frame was queued).
        frequency_range = frame[4] if len(frame) > 4 else (None, None)
        if all(bound is not None for bound in frequency_range):
            start_frequency, stop_frequency = frequency_range
        else:
            frequencies = x[np.isfinite(x)]
            start_frequency = float(frequencies.min()) if frequencies.size else ""
            stop_frequency = float(frequencies.max()) if frequencies.size else ""
        timestamp = datetime.fromtimestamp(wall_time).astimezone().isoformat(timespec="milliseconds")
        elapsed_ms = int((monotonic_time - self.started_at) * 1000)
        sweep_id = self.sweeps + 1
        # A sweep row is always written, including zero hits or invalid bins.
        self.writer.writerow((
            "", "", timestamp, elapsed_ms, "sweep", sweep_id,
            start_frequency, stop_frequency, self.threshold,
            len(x), int(valid.sum()), len(selected),
        ))
        self.writer.writerows(
            (float(x[i]), float(y[i]), timestamp, elapsed_ms, "signal", sweep_id,
             "", "", "", "", "", "") for i in selected
        )
        self.file.flush()
        self.sweeps = sweep_id
        self.rows += len(selected)

    def stop(self):
        stream, self.file = self.file, None
        if stream is not None:
            stream.close()


class RecordingWidget(QtWidgets.QWidget):
    """Independent recording controls; closing the dock only hides them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorder = CsvRecorder()
        settings = QtCore.QSettings()
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        directory_row = QtWidgets.QHBoxLayout()
        self.directoryEdit = QtWidgets.QLineEdit(
            settings.value("recording/directory", str(Path.home())), self
        )
        self.directoryButton = QtWidgets.QPushButton("...", self)
        self.directoryButton.setToolTip("Select output directory")
        self.directoryButton.clicked.connect(self.choose_directory)
        directory_row.addWidget(self.directoryEdit)
        directory_row.addWidget(self.directoryButton)
        form.addRow("Directory:", directory_row)
        self.thresholdSpinBox = QtWidgets.QDoubleSpinBox(self)
        self.thresholdSpinBox.setRange(-200, 100)
        self.thresholdSpinBox.setDecimals(1)
        self.thresholdSpinBox.setSuffix(" dB")
        self.thresholdSpinBox.setValue(settings.value("recording/threshold", -60.0, float))
        form.addRow("Trigger level:", self.thresholdSpinBox)
        self.delimiterEdit = QtWidgets.QLineEdit(settings.value("recording/delimiter", ";"), self)
        self.delimiterEdit.setMaxLength(1)
        self.delimiterEdit.setToolTip("One CSV separator character; default is semicolon")
        form.addRow("CSV separator:", self.delimiterEdit)
        layout.addLayout(form)
        buttons = QtWidgets.QHBoxLayout()
        self.startButton = QtWidgets.QPushButton("Start recording", self)
        self.stopButton = QtWidgets.QPushButton("Stop recording", self)
        self.startButton.clicked.connect(self.start_recording)
        self.stopButton.clicked.connect(self.stop_recording)
        buttons.addWidget(self.startButton)
        buttons.addWidget(self.stopButton)
        layout.addLayout(buttons)
        self.statusLabel = QtWidgets.QLabel("Recording stopped", self)
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.statusLabel)
        self.directoryEdit.editingFinished.connect(self.save_settings)
        self.thresholdSpinBox.valueChanged.connect(self.save_settings)
        self.delimiterEdit.editingFinished.connect(self.save_settings)
        self.update_controls()

    def save_settings(self):
        settings = QtCore.QSettings()
        settings.setValue("recording/directory", self.directoryEdit.text())
        settings.setValue("recording/threshold", self.thresholdSpinBox.value())
        settings.setValue("recording/delimiter", self.delimiterEdit.text())

    def choose_directory(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select recording directory", self.directoryEdit.text()
        )
        if directory:
            self.directoryEdit.setText(directory)
            self.save_settings()

    def update_controls(self):
        active = self.recorder.active
        for widget in (self.directoryEdit, self.directoryButton,
                       self.thresholdSpinBox, self.delimiterEdit, self.startButton):
            widget.setEnabled(not active)
        self.stopButton.setEnabled(active)

    def show_error(self, error):
        try:
            self.recorder.stop()
        except OSError:
            pass
        self.update_controls()
        self.statusLabel.setText("Recording error: {}".format(error))
        QtWidgets.QMessageBox.warning(self, "CSV recording", str(error))

    def start_recording(self):
        self.save_settings()
        try:
            self.recorder.start(self.directoryEdit.text(), self.thresholdSpinBox.value(),
                                self.delimiterEdit.text())
        except (OSError, ValueError, csv.Error) as error:
            self.show_error(error)
            return
        self.update_controls()
        self.statusLabel.setText("Recording: {}\nSweeps: 0 | Signals: 0 — waiting for spectrum data".format(
            self.recorder.path.name))
        self.statusLabel.setToolTip(str(self.recorder.path))

    @QtCore.Slot(object)
    def record_frame(self, frame):
        if not self.recorder.active:
            return
        try:
            self.recorder.write_frame(frame)
        except (OSError, ValueError, csv.Error) as error:
            self.show_error(error)
            return
        self.statusLabel.setText("Recording: {}\nSweeps: {} | Signals: {}".format(
            self.recorder.path.name, self.recorder.sweeps, self.recorder.rows))

    def stop_recording(self):
        try:
            self.recorder.stop()
        except OSError as error:
            self.show_error(error)
            return
        self.update_controls()
        self.statusLabel.setText("Recording stopped — sweeps: {} | signals: {}".format(
            self.recorder.sweeps, self.recorder.rows))
