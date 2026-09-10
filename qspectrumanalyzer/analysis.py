"""Offline viewing of threshold recordings, without acquiring SDR data."""

import csv
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from Qt import QtCore, QtWidgets


ROUNDING_MODES = {
    'nearest': ROUND_HALF_UP,
    'even': ROUND_HALF_EVEN,
    'down': ROUND_FLOOR,
    'up': ROUND_CEILING,
}


def round_frequencies(frequencies, decimals, mode):
    """Round Hz values to a decimal MHz grid, with explicit tie handling."""
    if decimals not in range(7) or mode not in ROUNDING_MODES:
        raise ValueError('Choose 0–6 MHz decimal places and a valid rounding mode.')
    step = Decimal(10) ** (6 - decimals)
    unique, inverse = np.unique(np.asarray(frequencies, dtype=float), return_inverse=True)
    # Round each distinct frequency once. Decimal avoids binary arithmetic
    # sending an exact decimal boundary or half-step to the wrong grid cell.
    rounded = np.array([float((Decimal(str(value)) / step).to_integral_value(
        rounding=ROUNDING_MODES[mode]) * step) for value in unique])
    return rounded[inverse]


class RecordingData:
    def __init__(self, sweeps, signals, legacy=False):
        self.sweeps = sweeps
        self.legacy = legacy
        # signal columns: sweep index, frequency Hz, power dB
        self.signals = np.asarray(signals, dtype=float).reshape((-1, 3))
        self.elapsed = np.array([s['elapsed_ms'] / 1000 for s in sweeps])
        self.wall = np.array([s['wall'] for s in sweeps])

    def rounded(self, decimals, mode):
        signals = self.signals.copy()
        signals[:, 1] = round_frequencies(signals[:, 1], decimals, mode)
        return RecordingData(self.sweeps, signals, self.legacy)

    def select(self, start, end, low, high, minimum, wall_range=None, frequency_source=None):
        sweep_mask = (self.elapsed >= start) & (self.elapsed <= end)
        if wall_range is not None:
            sweep_mask &= (self.wall >= wall_range[0]) & (self.wall <= wall_range[1])
        signals = self.signals
        frequencies = signals[:, 1] if frequency_source is None else frequency_source
        mask = (sweep_mask[signals[:, 0].astype(int)] &
                (frequencies >= low) & (frequencies <= high) &
                (signals[:, 2] >= minimum))
        return np.flatnonzero(sweep_mask), signals[mask]

    def band_series(self, sweep_indices, signals, low, high):
        """One maximum per observed sweep; NaN never invents a below-threshold value."""
        peaks = np.full(len(self.sweeps), -np.inf)
        band = signals[(signals[:, 1] >= low) & (signals[:, 1] <= high)]
        np.maximum.at(peaks, band[:, 0].astype(int), band[:, 2])
        peaks[~np.isfinite(peaks)] = np.nan
        return peaks[sweep_indices]


def read_recording(path, delimiter=None, cancelled=lambda: False):
    sweeps, signals, indices, expected = [], [], {}, []
    with open(path, encoding='utf-8-sig', newline='') as stream:
        header = stream.readline()
        if not header:
            raise ValueError('The file is empty.')
        required = {'frequency_hz', 'power_db', 'system_time', 'elapsed_ms'}
        if delimiter is None:
            # Sniffer can mistake repeated letters in column names for a
            # delimiter. Match the known header using actual punctuation.
            candidates = set(c for c in header if not c.isalnum() and c not in '_"\r\n')
            delimiter = next((c for c in candidates
                              if required <= set(next(csv.reader([header], delimiter=c)))), None)
            if delimiter is None:
                raise ValueError('Cannot detect CSV separator or recording columns.')
        if len(delimiter) != 1:
            raise ValueError('CSV separator must be one character.')
        stream.seek(0)
        reader = csv.DictReader(stream, delimiter=delimiter)
        fields = set(reader.fieldnames or [])
        if not required <= fields:
            raise ValueError('Missing CSV columns. Check the separator and recording format.')
        legacy = 'record_type' not in fields
        if not legacy and not {'sweep_id', 'start_frequency_hz', 'stop_frequency_hz',
                               'threshold_db', 'bin_count', 'valid_bin_count', 'hit_count'} <= fields:
            raise ValueError('Incomplete sweep metadata columns.')
        for row in reader:
            if cancelled():
                raise InterruptedError('Loading cancelled')
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError('Incomplete row or wrong separator')
                elapsed = float(row['elapsed_ms'])
                wall = datetime.fromisoformat(row['system_time']).timestamp()
                if not np.isfinite(elapsed) or elapsed < 0 or not np.isfinite(wall):
                    raise ValueError('Invalid timestamp')
                kind = 'signal' if legacy else row['record_type']
                key = (elapsed, row['system_time']) if legacy else row['sweep_id']
                if kind == 'sweep' or (legacy and key not in indices):
                    if key in indices:
                        raise ValueError('Duplicate sweep ID')
                    if not legacy and int(key) < 1:
                        raise ValueError('Invalid sweep ID')
                    sweep = dict(id=key if not legacy else len(sweeps) + 1,
                                 elapsed_ms=elapsed, wall=wall, system_time=row['system_time'],
                                 start=None, stop=None, threshold=None, bins=None, valid=None)
                    count = None
                    if not legacy:
                        sweep.update(start=float(row['start_frequency_hz']) if row['start_frequency_hz'] else None,
                                     stop=float(row['stop_frequency_hz']) if row['stop_frequency_hz'] else None,
                                     threshold=float(row['threshold_db']), bins=int(row['bin_count']),
                                     valid=int(row['valid_bin_count']))
                        count = int(row['hit_count'])
                        if not 0 <= count <= sweep['valid'] <= sweep['bins']:
                            raise ValueError('Invalid bin counts')
                        if not np.isfinite(sweep['threshold']):
                            raise ValueError('Invalid threshold')
                        bounds = (sweep['start'], sweep['stop'])
                        if (bounds[0] is None) != (bounds[1] is None):
                            raise ValueError('Incomplete frequency range')
                        if any(b is not None and not np.isfinite(b) for b in bounds):
                            raise ValueError('Invalid frequency range')
                        if all(b is not None for b in bounds) and bounds[0] > bounds[1]:
                            raise ValueError('Reversed frequency range')
                    indices[key] = len(sweeps)
                    sweeps.append(sweep)
                    expected.append(count)
                if kind == 'signal':
                    if key not in indices:
                        raise ValueError('Signal references an unknown sweep')
                    index = indices[key]
                    sweep = sweeps[index]
                    if elapsed != sweep['elapsed_ms'] or wall != sweep['wall']:
                        raise ValueError('Signal timestamp differs from its sweep')
                    frequency, power = float(row['frequency_hz']), float(row['power_db'])
                    if not np.isfinite(frequency) or not np.isfinite(power):
                        raise ValueError('Non-finite signal value')
                    signals.append((index, frequency, power))
                elif kind != 'sweep':
                    raise ValueError('Unknown record_type')
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError('CSV line {}: {}'.format(reader.line_num, error)) from error
    if not sweeps:
        raise ValueError('The recording contains no sweeps or signals.')
    counts = np.bincount([int(s[0]) for s in signals], minlength=len(sweeps))
    for i, sweep in enumerate(sweeps):
        if expected[i] is not None and expected[i] != counts[i]:
            raise ValueError('Incomplete sweep {}: expected {} signals, found {}. '
                             'Stop recording before opening the file.'.format(
                                 sweep['id'], expected[i], counts[i]))
        sweep['hits'] = int(counts[i])
    return RecordingData(sweeps, signals, legacy)


class RecordingTableModel(QtCore.QAbstractTableModel):
    """All matching file records, with cells formatted only when requested."""

    headers = ['Type', 'Sweep', 'System time', 'Elapsed [ms]', 'Frequency [MHz]',
               'Power [dB]', 'Frequency [Hz]', 'Start [MHz]', 'Stop [MHz]',
               'Trigger [dB]', 'Bins', 'Valid bins', 'Hits']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recording = None
        self.signals = np.empty((0, 3))
        self.rows = np.empty(0, dtype=int)
        self.frequency_decimals = 6

    def set_records(self, recording, sweep_indices, signals):
        self.beginResetModel()
        self.recording = recording
        self.signals = signals
        # Negative references represent sweep records, nonnegative ones signals.
        # Legacy files contain only signals: do not invent metadata rows.
        sweeps = np.empty(0, dtype=int) if recording.legacy else sweep_indices
        refs = np.concatenate((-sweeps - 1, np.arange(len(signals))))
        keys = np.concatenate((sweeps * 2, signals[:, 0].astype(int) * 2 + 1))
        self.rows = refs[np.argsort(keys, kind='stable')]
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            return self.headers[section] if orientation == QtCore.Qt.Horizontal else str(section + 1)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or role not in (QtCore.Qt.DisplayRole, QtCore.Qt.UserRole,
                                              QtCore.Qt.ToolTipRole):
            return None
        ref = self.rows[index.row()]
        is_signal = ref >= 0
        signal = self.signals[ref] if is_signal else None
        sweep = self.recording.sweeps[int(signal[0]) if is_signal else -ref - 1]
        frequency = float(signal[1]) if is_signal else None
        values = ['signal' if is_signal else 'sweep', int(sweep['id']), sweep['system_time'],
                  sweep['elapsed_ms'], frequency / 1e6 if is_signal else None,
                  float(signal[2]) if is_signal else None, frequency,
                  sweep['start'] / 1e6 if not is_signal and sweep['start'] is not None else None,
                  sweep['stop'] / 1e6 if not is_signal and sweep['stop'] is not None else None,
                  sweep['threshold'] if not is_signal else None,
                  sweep['bins'] if not is_signal else None,
                  sweep['valid'] if not is_signal else None,
                  sweep['hits'] if not is_signal else None]
        value = values[index.column()]
        if role == QtCore.Qt.UserRole:
            return value
        if value is None:
            return '—'
        if role == QtCore.Qt.ToolTipRole:
            return str(value)
        if index.column() == 4:
            return '{:.{}f}'.format(value, self.frequency_decimals)
        if index.column() in (7, 8):
            return '{:.6f}'.format(value)
        if index.column() in (5, 9):
            return '{:.3f}'.format(value)
        return str(value)


def signal_markers(times, frequencies, powers, power_limits):
    """Bound rendering cost while retaining actual coordinates of strong hits."""
    selected = np.arange(len(powers))
    if len(powers) > 30000:
        tx = np.clip(((times - times.min()) / max(np.ptp(times), .001) * 249).astype(int), 0, 249)
        fy = np.clip(((frequencies - frequencies.min()) /
                      max(np.ptp(frequencies), 1) * 119).astype(int), 0, 119)
        cells = tx * 120 + fy
        strongest_first = np.argsort(powers, kind='stable')[::-1]
        _, first = np.unique(cells[strongest_first], return_index=True)
        selected = strongest_first[first]
    # Draw stronger/larger points last so weaker ones cannot cover them.
    selected = selected[np.argsort(powers[selected], kind='stable')]
    low, high = power_limits
    normalized = (np.clip((powers[selected] - low) / (high - low), 0, 1)
                  if high > low else np.full(len(selected), .5))
    # Discrete diameters keep the scatter symbol cache bounded.
    sizes = 4 + np.rint(normalized * 12)
    return times[selected], frequencies[selected], powers[selected], sizes


class RecordingLoader(QtCore.QThread):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, path, delimiter, parent):
        super().__init__(parent)
        self.path, self.delimiter = path, delimiter

    def run(self):
        try:
            result = read_recording(self.path, self.delimiter, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.loaded.emit(result)
        except InterruptedError:
            pass
        except (OSError, ValueError, csv.Error) as error:
            self.failed.emit(str(error))


class RecordingAnalysisWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Recording analysis — QSpectrumAnalyzer')
        self.resize(1200, 900)
        self.data = None
        self.preview_data = None
        self.preview_key = None
        self.graph_data = None
        self.applied_rounding = None
        self.loader = None
        self.updating = False
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        file_row = QtWidgets.QHBoxLayout()
        self.openButton = QtWidgets.QPushButton('Open CSV...')
        self.openButton.clicked.connect(self.open_file)
        self.separatorEdit = QtWidgets.QLineEdit()
        self.separatorEdit.setPlaceholderText('Auto')
        self.separatorEdit.setMaxLength(1)
        self.separatorEdit.setMaximumWidth(65)
        self.fileLabel = QtWidgets.QLabel('No recording loaded')
        self.fileLabel.setTextFormat(QtCore.Qt.PlainText)
        file_row.addWidget(self.openButton)
        file_row.addWidget(QtWidgets.QLabel('Separator:'))
        file_row.addWidget(self.separatorEdit)
        file_row.addWidget(self.fileLabel, 1)
        layout.addLayout(file_row)
        filters = QtWidgets.QGridLayout()
        self.startSpin = self.spin(0, 1e12, 3, ' s')
        self.endSpin = self.spin(0, 1e12, 3, ' s')
        self.lowSpin = self.spin(0, 1e9, 6, ' MHz')
        self.highSpin = self.spin(0, 1e9, 6, ' MHz')
        self.minimumSpin = self.spin(-1000, 1000, 1, ' dB')
        for col, (label, widget) in enumerate([
            ('From recording start:', self.startSpin), ('To:', self.endSpin),
            ('Frequency from:', self.lowSpin), ('To:', self.highSpin), ('Min level:', self.minimumSpin)
        ]):
            filters.addWidget(QtWidgets.QLabel(label), 0, col)
            filters.addWidget(widget, 1, col)
        self.wallCheck = QtWidgets.QCheckBox('Also filter system time')
        self.wallStart = QtWidgets.QDateTimeEdit()
        self.wallEnd = QtWidgets.QDateTimeEdit()
        for widget in (self.wallStart, self.wallEnd):
            widget.setDisplayFormat('yyyy-MM-dd HH:mm:ss.zzz')
            widget.setCalendarPopup(True)
            widget.setEnabled(False)
            widget.dateTimeChanged.connect(self.schedule_refresh)
        self.wallCheck.toggled.connect(self.wallStart.setEnabled)
        self.wallCheck.toggled.connect(self.wallEnd.setEnabled)
        self.wallCheck.toggled.connect(self.schedule_refresh)
        self.resetButton = QtWidgets.QPushButton('Full recording')
        self.resetButton.clicked.connect(self.reset_filters)
        filters.addWidget(self.wallCheck, 2, 0)
        filters.addWidget(self.wallStart, 2, 1, 1, 2)
        filters.addWidget(self.wallEnd, 2, 3)
        filters.addWidget(self.resetButton, 2, 4)
        layout.addLayout(filters)
        self.notice = QtWidgets.QLabel('Open a completed CSV recording to select a viewing period.')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.fileTab = QtWidgets.QWidget()
        file_layout = QtWidgets.QVBoxLayout(self.fileTab)
        rounding_row = QtWidgets.QHBoxLayout()
        self.roundingCheck = QtWidgets.QCheckBox('Round signal frequencies')
        self.roundingDecimals = QtWidgets.QSpinBox()
        self.roundingDecimals.setRange(0, 6)
        self.roundingDecimals.setValue(3)
        self.roundingDecimals.setToolTip('Decimal places in MHz: 3 = 1 kHz, 6 = 1 Hz')
        self.roundingMode = QtWidgets.QComboBox()
        for label, mode in [('Nearest (half up)', 'nearest'), ('Nearest (half to even)', 'even'),
                            ('Down (floor)', 'down'), ('Up (ceiling)', 'up')]:
            self.roundingMode.addItem(label, mode)
        self.applyRoundingButton = QtWidgets.QPushButton('Update graphs from table')
        self.originalButton = QtWidgets.QPushButton('Restore original frequencies')
        rounding_row.addWidget(self.roundingCheck)
        rounding_row.addWidget(QtWidgets.QLabel('MHz decimal places:'))
        rounding_row.addWidget(self.roundingDecimals)
        rounding_row.addWidget(self.roundingMode)
        rounding_row.addWidget(self.applyRoundingButton)
        rounding_row.addWidget(self.originalButton)
        file_layout.addLayout(rounding_row)
        self.roundingStatus = QtWidgets.QLabel('Graphs: original frequencies')
        self.roundingStatus.setWordWrap(True)
        file_layout.addWidget(self.roundingStatus)
        self.roundingCheck.toggled.connect(self.rounding_changed)
        self.roundingDecimals.valueChanged.connect(self.rounding_changed)
        self.roundingMode.currentIndexChanged.connect(self.rounding_changed)
        self.applyRoundingButton.clicked.connect(self.apply_rounding)
        self.originalButton.clicked.connect(self.restore_original_frequencies)
        self.roundingDecimals.setEnabled(False)
        self.roundingMode.setEnabled(False)
        self.applyRoundingButton.setEnabled(False)
        self.originalButton.setEnabled(False)
        self.tableSummary = QtWidgets.QLabel()
        self.tableSummary.setWordWrap(True)
        file_layout.addWidget(self.tableSummary)
        self.tableModel = RecordingTableModel(self)
        self.tableProxy = QtCore.QSortFilterProxyModel(self)
        self.tableProxy.setSourceModel(self.tableModel)
        self.tableProxy.setSortRole(QtCore.Qt.UserRole)
        self.table = QtWidgets.QTableView()
        self.table.setModel(self.tableProxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, QtCore.Qt.AscendingOrder)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setColumnWidth(2, 280)
        for col in (3, 4, 6, 7, 8):
            self.table.setColumnWidth(col, 135)
        file_layout.addWidget(self.table, 1)
        self.tabs.addTab(self.fileTab, 'File data')
        self.graphTab = QtWidgets.QWidget()
        graph_layout = QtWidgets.QVBoxLayout(self.graphTab)
        self.tabs.addTab(self.graphTab, 'Analysis')
        self.overview = pg.PlotWidget()
        self.overview.setMaximumHeight(130)
        self.overview.setLabel('left', 'Recorded hits')
        self.overview.setLabel('bottom', 'Time from recording start', units='s')
        self.overviewCurve = self.overview.plot(pen=None, symbol='o', symbolSize=3)
        self.period = pg.LinearRegionItem((0, 1))
        self.overview.addItem(self.period)
        self.period.sigRegionChangeFinished.connect(self.period_changed)
        graph_layout.addWidget(self.overview)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        graph_layout.addWidget(split, 1)
        self.graphics = pg.GraphicsLayoutWidget()
        self.mapPlot = self.graphics.addPlot()
        self.mapPlot.setMouseEnabled(x=False, y=True)
        self.mapPlot.setToolTip('Mouse wheel: zoom frequency. Drag: move frequency range. Select time above.')
        self.mapPlot.setLabel('bottom', 'Time from recording start', units='s')
        self.mapPlot.setLabel('left', 'Frequency', units='MHz')
        self.mapPlot.setTitle('Recorded signals: larger point = stronger signal; click to select frequency')
        self.mapScatter = pg.ScatterPlotItem(
            pen=None, brush=pg.mkBrush(255, 220, 60), symbol='o', pxMode=True,
            hoverable=True, tip=lambda x, y, data: 'Time: {:.3f} s\nFrequency: {:.6f} MHz\nPower: {:.3f} dB'.format(x, y, data))
        self.mapPlot.addItem(self.mapScatter)
        self.selectedBand = pg.LinearRegionItem(
            (0, 0), orientation='horizontal', movable=False,
            brush=(255, 255, 255, 20), pen=pg.mkPen((180, 180, 180), style=QtCore.Qt.DashLine))
        self.mapPlot.addItem(self.selectedBand, ignoreBounds=True)
        self.mapPlot.scene().sigMouseClicked.connect(self.map_clicked)
        split.addWidget(self.graphics)
        lower = QtWidgets.QWidget()
        lower_layout = QtWidgets.QVBoxLayout(lower)
        band_row = QtWidgets.QHBoxLayout()
        self.bandCenter = self.spin(0, 1e9, 6, ' MHz', connect=False)
        self.bandWidth = self.spin(0, 1e9, 3, ' kHz', connect=False)
        self.bandCenter.valueChanged.connect(self.schedule_refresh)
        self.bandWidth.valueChanged.connect(self.schedule_refresh)
        band_row.addWidget(QtWidgets.QLabel('Selected frequency:'))
        band_row.addWidget(self.bandCenter)
        band_row.addWidget(QtWidgets.QLabel('Band width (0 = nearest recorded frequency):'))
        band_row.addWidget(self.bandWidth)
        lower_layout.addLayout(band_row)
        self.levelPlot = pg.PlotWidget()
        self.levelPlot.setMouseEnabled(x=False, y=True)
        self.levelPlot.setToolTip('Mouse wheel: zoom signal level. Drag: move level range. Select time above.')
        self.levelPlot.setLabel('bottom', 'Time from recording start', units='s')
        self.levelPlot.setLabel('left', 'Peak level in selected band', units='dB')
        self.levelPlot.setXLink(self.mapPlot)
        self.levelCurve = self.levelPlot.plot(pen=None, symbol='o', symbolSize=6, symbolBrush='y')
        lower_layout.addWidget(self.levelPlot)
        split.addWidget(lower)
        split.setSizes([380, 350])
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)

    def spin(self, low, high, decimals, suffix, connect=True):
        widget = QtWidgets.QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setDecimals(decimals)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        if connect:
            widget.valueChanged.connect(self.schedule_refresh)
        return widget

    def schedule_refresh(self, *args):
        if not self.updating:
            self.timer.start()

    def open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Open recording', '', 'CSV files (*.csv);;All files (*)')
        if path:
            self.load_file(path, self.separatorEdit.text() or None)

    def load_file(self, path, delimiter=None):
        if self.loader is not None and self.loader.isRunning():
            return
        if self.loader is not None:
            self.loader.deleteLater()
        self.openButton.setEnabled(False)
        self.statusBar().showMessage('Loading CSV...')
        self.loader = RecordingLoader(path, delimiter, self)
        self.loader.loaded.connect(self.loaded)
        self.loader.failed.connect(self.load_failed)
        self.loader.finished.connect(lambda: self.openButton.setEnabled(True))
        self.loader.start()

    @QtCore.Slot(object)
    def loaded(self, data):
        self.data = data
        self.graph_data = None
        self.applied_rounding = None
        self.preview_data = None
        self.preview_key = None
        self.roundingCheck.blockSignals(True)
        self.roundingCheck.setChecked(False)
        self.roundingCheck.blockSignals(False)
        self.fileLabel.setText(str(Path(self.loader.path)))
        self.overviewCurve.setData(data.elapsed, [s['hits'] for s in data.sweeps])
        self.reset_filters()

    def rounding_settings(self):
        return ((self.roundingDecimals.value(), self.roundingMode.currentData())
                if self.roundingCheck.isChecked() else None)

    def table_data(self):
        settings = self.rounding_settings()
        key = (id(self.data), settings)
        if key != self.preview_key:
            self.preview_data = self.data.rounded(*settings) if settings else self.data
            self.preview_key = key
        return self.preview_data

    def rounding_changed(self, *args):
        # Preview is intentionally separate from applied graph data. Ordinary
        # filter changes must not apply pending rounding to the graphs either.
        if self.data is not None:
            self.refresh_table()
        self.update_rounding_status()

    def update_rounding_status(self):
        settings = self.rounding_settings()
        self.roundingDecimals.setEnabled(settings is not None)
        self.roundingMode.setEnabled(settings is not None)
        self.applyRoundingButton.setEnabled(self.data is not None)
        self.originalButton.setEnabled(self.data is not None)
        description = ('original frequencies' if self.applied_rounding is None else
                       '{} MHz decimal places, {}'.format(*self.applied_rounding))
        pending = ' — table changes not yet applied' if settings != self.applied_rounding else ''
        self.roundingStatus.setText('Graphs: {}{}'.format(description, pending))

    def apply_rounding(self):
        if self.data is None:
            return
        self.applied_rounding = self.rounding_settings()
        self.graph_data = self.table_data()
        if self.applied_rounding is not None:
            decimals, mode = self.applied_rounding
            self.updating = True
            # Include rounded edge bins instead of clipping them at the old
            # frequency limits. Time and power filters remain as selected.
            self.lowSpin.setValue(round_frequencies([self.lowSpin.value() * 1e6], decimals, 'down')[0] / 1e6)
            self.highSpin.setValue(round_frequencies([self.highSpin.value() * 1e6], decimals, 'up')[0] / 1e6)
            self.bandCenter.setValue(round_frequencies([self.bandCenter.value() * 1e6], decimals, mode)[0] / 1e6)
            self.updating = False
        self.refresh()

    def restore_original_frequencies(self):
        if self.data is None:
            return
        self.roundingCheck.blockSignals(True)
        self.roundingCheck.setChecked(False)
        self.roundingCheck.blockSignals(False)
        self.applied_rounding = None
        self.graph_data = None
        self.refresh()

    def selection(self, data, original_frequencies=False):
        wall_range = None
        if self.wallCheck.isChecked():
            wall_range = (self.wallStart.dateTime().toMSecsSinceEpoch() / 1000,
                          self.wallEnd.dateTime().toMSecsSinceEpoch() / 1000)
        return data.select(self.startSpin.value(), self.endSpin.value(),
                           self.lowSpin.value() * 1e6, self.highSpin.value() * 1e6,
                           self.minimumSpin.value(), wall_range,
                           self.data.signals[:, 1] if original_frequencies else None)

    def refresh_table(self):
        data = self.table_data()
        indices, signals = self.selection(data, original_frequencies=True)
        settings = self.rounding_settings()
        self.tableModel.frequency_decimals = settings[0] if settings else 6
        self.tableModel.set_records(data, indices, signals)
        self.tableSummary.setText(
            '{} file records: {} signals{}. Period filters apply to all rows; '
            'table frequency filters use original values. '
            'Rounding preserves individual rows; coincident frequencies use maximum power per sweep in graphs.'.format(
                self.tableModel.rowCount(), len(signals),
                '' if data.legacy else ', {} sweeps'.format(len(indices))))
        self.update_rounding_status()

    def load_failed(self, message):
        self.statusBar().showMessage('Could not open recording')
        QtWidgets.QMessageBox.warning(self, 'Recording analysis', message)

    def reset_filters(self):
        if self.data is None:
            return
        self.updating = True
        data = self.graph_data if self.graph_data is not None else self.data
        self.startSpin.setValue(float(data.elapsed.min()))
        self.endSpin.setValue(float(data.elapsed.max()))
        bounds = [b for s in data.sweeps for b in (s['start'], s['stop']) if b is not None]
        bounds.extend(data.signals[:, 1])
        self.lowSpin.setValue(min(bounds) / 1e6 if bounds else 0)
        self.highSpin.setValue(max(bounds) / 1e6 if bounds else 1)
        self.minimumSpin.setValue(float(data.signals[:, 2].min()) - 1 if len(data.signals) else -200)
        self.wallCheck.setChecked(False)
        self.wallStart.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(round(data.wall.min() * 1000)))
        self.wallEnd.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(round(data.wall.max() * 1000)))
        self.bandCenter.setValue(float(data.signals[0, 1] / 1e6) if len(data.signals) else self.lowSpin.value())
        self.bandWidth.setValue(0)
        self.period.setBounds((self.startSpin.value(), max(self.endSpin.value(), self.startSpin.value() + .001)))
        self.overview.enableAutoRange()
        self.updating = False
        self.refresh()

    def period_changed(self):
        if self.updating:
            return
        start, end = self.period.getRegion()
        self.updating = True
        self.startSpin.setValue(start)
        self.endSpin.setValue(end)
        self.updating = False
        self.refresh()

    def refresh(self):
        if self.data is None:
            return
        start, end = self.startSpin.value(), self.endSpin.value()
        low, high = self.lowSpin.value() * 1e6, self.highSpin.value() * 1e6
        wall_range = None
        if self.wallCheck.isChecked():
            wall_range = (self.wallStart.dateTime().toMSecsSinceEpoch() / 1000,
                          self.wallEnd.dateTime().toMSecsSinceEpoch() / 1000)
        if start > end or low > high or (wall_range and wall_range[0] > wall_range[1]):
            self.statusBar().showMessage('Invalid range: start must not exceed end.')
            return
        self.updating = True
        self.period.setRegion((start, end))
        self.updating = False
        graph_data = self.graph_data if self.graph_data is not None else self.data
        indices, signals = self.selection(graph_data)
        time_span, freq_span = max(end - start, .001), max(high - low, 1)
        # Keep the size scale fixed across period/frequency filters.
        all_powers = graph_data.signals[:, 2]
        limits = ((float(all_powers.min()), float(all_powers.max()))
                  if len(all_powers) else (0, 0))
        if len(signals):
            times = self.data.elapsed[signals[:, 0].astype(int)]
            marker_x, marker_f, marker_power, sizes = signal_markers(times, signals[:, 1], signals[:, 2], limits)
            self.mapScatter.setData(x=marker_x, y=marker_f / 1e6, size=sizes, data=marker_power)
        else:
            self.mapScatter.clear()
        if not len(all_powers):
            scale = 'no recorded signals'
        elif limits[0] == limits[1]:
            scale = '10 px = {:.1f} dB'.format(limits[0])
        else:
            scale = 'diameter 4 px = {:.1f} dB; 16 px = {:.1f} dB (full-file scale)'.format(*limits)
        self.mapPlot.setTitle('Larger point = stronger signal | {} | Hover for values'.format(scale))
        self.mapPlot.setXRange(start, start + time_span, padding=0)
        self.mapPlot.setYRange(low / 1e6, (low + freq_span) / 1e6, padding=0)
        center = self.bandCenter.value() * 1e6
        half = self.bandWidth.value() * 1000 / 2
        if half == 0 and len(signals):
            center = float(signals[np.argmin(abs(signals[:, 1] - center)), 1])
            self.updating = True
            self.bandCenter.setValue(center / 1e6)
            self.updating = False
        self.selectedBand.setRegion(((center - half) / 1e6, (center + half) / 1e6))
        peaks = self.data.band_series(indices, signals, center - half, center + half)
        self.selected_peaks = peaks
        finite = np.isfinite(peaks)
        self.levelCurve.setData(self.data.elapsed[indices][finite], peaks[finite])
        self.levelPlot.enableAutoRange(axis='y')
        self.refresh_table()
        self.notice.setText(('Legacy CSV: only recorded hits are known; gaps do not prove signal absence. '
                             if self.data.legacy else
                             'Sweep rows include zero-hit passes. Missing levels are not reconstructed. ') +
                            'Map point size shows level; dense maps retain the strongest hit per display cell. '
                            'Graph points are not connected across gaps. '
                            'System time filter uses local time.')
        self.statusBar().showMessage('{} sweeps | {} matching signals | {} table rows'.format(
            len(indices), len(signals), self.tableModel.rowCount()))

    def map_clicked(self, event):
        if self.data is None or event.button() != QtCore.Qt.LeftButton:
            return
        if self.mapPlot.sceneBoundingRect().contains(event.scenePos()):
            point = self.mapPlot.getViewBox().mapSceneToView(event.scenePos())
            self.bandCenter.setValue(max(0, point.y()))

    def closeEvent(self, event):
        self.timer.stop()
        if self.loader is not None and self.loader.isRunning():
            self.loader.requestInterruption()
            self.loader.wait()
        super().closeEvent(event)
