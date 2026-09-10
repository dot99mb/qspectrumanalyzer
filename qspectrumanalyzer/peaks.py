import numpy as np
from Qt import QtCore, QtWidgets
from qspectrumanalyzer.utils import round_frequencies


class NumericTableWidgetItem(QtWidgets.QTableWidgetItem):
    """Table item sorting by numeric data when available."""
    def __lt__(self, other):
        value = self.data(QtCore.Qt.UserRole)
        other_value = other.data(QtCore.Qt.UserRole)
        if value is not None and other_value is not None:
            return value < other_value
        return super().__lt__(other)


def find_peak_frequencies(x, y, limit=None, min_power=None):
    """Return strongest local maxima as (frequency, power) pairs."""
    if x is None or y is None:
        return []

    x = np.asarray(x)
    y = np.asarray(y)
    size = min(len(x), len(y))
    if size < 3:
        return []

    x = x[:size]
    y = y[:size]
    finite = np.isfinite(x) & np.isfinite(y)
    if not finite.any():
        return []

    peaks = np.flatnonzero(
        finite[1:-1] &
        (y[1:-1] > y[:-2]) &
        (y[1:-1] >= y[2:])
    ) + 1
    if not len(peaks):
        return []

    if min_power is not None:
        peaks = peaks[y[peaks] >= min_power]
    if not len(peaks):
        return []

    peaks = peaks[np.argsort(y[peaks])[::-1]]
    return [(float(x[index]), float(y[index])) for index in peaks[:limit]]


def find_signal_bands(x, y, limit=None, band_floor=None, min_power=None):
    """Return contiguous above-threshold signal bands sorted by peak power."""
    if x is None or y is None or band_floor is None:
        return []

    x = np.asarray(x)
    y = np.asarray(y)
    size = min(len(x), len(y))
    if size < 1:
        return []

    x = x[:size]
    y = y[:size]
    mask = np.isfinite(x) & np.isfinite(y) & (y >= band_floor)
    if not mask.any():
        return []

    if size > 1:
        steps = np.diff(x)
        steps = steps[np.isfinite(steps) & (steps > 0)]
        bin_width = float(np.median(steps)) if len(steps) else 1.0
    else:
        bin_width = 1.0

    edges = np.flatnonzero(np.diff(mask.astype(int)) != 0) + 1
    groups = np.split(np.arange(size), edges)
    bands = []
    for group in groups:
        if not mask[group[0]]:
            continue

        local_peak = group[np.argmax(y[group])]
        peak_power = float(y[local_peak])
        if min_power is not None and peak_power < min_power:
            continue

        start = float(x[group[0]] - bin_width / 2)
        stop = float(x[group[-1]] + bin_width / 2)
        bands.append((start, stop, float(x[local_peak]), peak_power))

    bands.sort(key=lambda band: band[3], reverse=True)
    return bands[:limit]


class PeakListWidget(QtWidgets.QWidget):
    """Table showing peak frequencies from the average spectrum."""
    refresh_requested = QtCore.Signal()
    auto_refresh_toggled = QtCore.Signal(bool)
    refresh_interval_changed = QtCore.Signal(float)
    source_changed = QtCore.Signal(str)
    min_power_changed = QtCore.Signal(float)
    band_floor_changed = QtCore.Signal(float)
    bands_toggled = QtCore.Signal(bool)
    peak_selected = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.limit = None
        self.total_found = 0
        self.last_x = None
        self.last_y = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)

        self.sourceComboBox = QtWidgets.QComboBox(self)
        self.sourceComboBox.addItem("Average", "average")
        self.sourceComboBox.addItem("Max hold", "peak_hold_max")
        self.sourceComboBox.currentIndexChanged.connect(
            lambda index: self.source_changed.emit(self.sourceComboBox.itemData(index))
        )
        controls_layout.addWidget(self.sourceComboBox)

        self.refreshButton = QtWidgets.QPushButton(self)
        self.refreshButton.setText("Refresh")
        self.refreshButton.clicked.connect(lambda checked=False: self.refresh_requested.emit())
        controls_layout.addWidget(self.refreshButton)

        self.clearButton = QtWidgets.QPushButton(self)
        self.clearButton.setText("Clear")
        self.clearButton.clicked.connect(lambda checked=False: self.clear())
        controls_layout.addWidget(self.clearButton)

        self.autoRefreshCheckBox = QtWidgets.QCheckBox(self)
        self.autoRefreshCheckBox.setText("Auto")
        self.autoRefreshCheckBox.setChecked(False)
        self.autoRefreshCheckBox.toggled.connect(self.auto_refresh_toggled)
        controls_layout.addWidget(self.autoRefreshCheckBox)

        self.refreshIntervalSpinBox = QtWidgets.QDoubleSpinBox(self)
        self.refreshIntervalSpinBox.setDecimals(1)
        self.refreshIntervalSpinBox.setMinimum(0.1)
        self.refreshIntervalSpinBox.setMaximum(60.0)
        self.refreshIntervalSpinBox.setSingleStep(0.1)
        self.refreshIntervalSpinBox.setValue(1.0)
        self.refreshIntervalSpinBox.setSuffix(" s")
        self.refreshIntervalSpinBox.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.refreshIntervalSpinBox.valueChanged.connect(self.refresh_interval_changed)
        controls_layout.addWidget(self.refreshIntervalSpinBox)

        layout.addLayout(controls_layout)

        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)

        self.minPowerLabel = QtWidgets.QLabel(self)
        self.minPowerLabel.setText("Trigger level:")
        filter_layout.addWidget(self.minPowerLabel)

        self.minPowerSpinBox = QtWidgets.QDoubleSpinBox(self)
        self.minPowerSpinBox.setDecimals(1)
        self.minPowerSpinBox.setMinimum(-200.0)
        self.minPowerSpinBox.setMaximum(100.0)
        self.minPowerSpinBox.setSingleStep(1.0)
        self.minPowerSpinBox.setValue(QtCore.QSettings().value("peaks/trigger_level", -120.0, float))
        self.minPowerSpinBox.setSuffix(" dB")
        self.minPowerSpinBox.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.minPowerSpinBox.valueChanged.connect(self.min_power_changed)
        self.minPowerSpinBox.valueChanged.connect(
            lambda value: QtCore.QSettings().setValue("peaks/trigger_level", value))
        filter_layout.addWidget(self.minPowerSpinBox)

        self.signalBandsCheckBox = QtWidgets.QCheckBox(self)
        self.signalBandsCheckBox.setText("Signal bands")
        self.signalBandsCheckBox.toggled.connect(self.bands_toggled)
        filter_layout.addWidget(self.signalBandsCheckBox)

        layout.addLayout(filter_layout)

        bands_layout = QtWidgets.QHBoxLayout()
        bands_layout.setContentsMargins(0, 0, 0, 0)

        self.bandFloorLabel = QtWidgets.QLabel(self)
        self.bandFloorLabel.setText("Band floor:")
        bands_layout.addWidget(self.bandFloorLabel)

        self.bandFloorSpinBox = QtWidgets.QDoubleSpinBox(self)
        self.bandFloorSpinBox.setDecimals(1)
        self.bandFloorSpinBox.setMinimum(-200.0)
        self.bandFloorSpinBox.setMaximum(100.0)
        self.bandFloorSpinBox.setSingleStep(1.0)
        self.bandFloorSpinBox.setValue(-60.0)
        self.bandFloorSpinBox.setSuffix(" dB")
        self.bandFloorSpinBox.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.bandFloorSpinBox.valueChanged.connect(self.band_floor_changed)
        bands_layout.addWidget(self.bandFloorSpinBox)

        layout.addLayout(bands_layout)

        settings = QtCore.QSettings()
        rounding_layout = QtWidgets.QHBoxLayout()
        self.roundingCheckBox = QtWidgets.QCheckBox("Round MHz")
        self.roundingCheckBox.setChecked(bool(settings.value("peaks/rounding", 0, int)))
        self.roundingDecimalsSpinBox = QtWidgets.QSpinBox()
        self.roundingDecimalsSpinBox.setRange(0, 6)
        self.roundingDecimalsSpinBox.setValue(settings.value("peaks/rounding_decimals", 3, int))
        self.roundingDecimalsSpinBox.setToolTip("Decimal places in MHz; 3 = 1 kHz")
        self.roundingModeComboBox = QtWidgets.QComboBox()
        for label, mode in [("Nearest (half up)", "nearest"), ("Nearest (half to even)", "even"),
                            ("Down", "down"), ("Up", "up")]:
            self.roundingModeComboBox.addItem(label, mode)
        mode_index = self.roundingModeComboBox.findData(settings.value("peaks/rounding_mode", "nearest"))
        self.roundingModeComboBox.setCurrentIndex(max(0, mode_index))
        for widget in (self.roundingCheckBox, self.roundingDecimalsSpinBox, self.roundingModeComboBox):
            rounding_layout.addWidget(widget)
        layout.addLayout(rounding_layout)
        self.followCursorCheckBox = QtWidgets.QCheckBox("Follow cursor")
        self.followCursorCheckBox.setToolTip(
            "Link table selection and spectrum cursor. Left-click directly on a listed peak to select it.")
        self.followCursorCheckBox.setChecked(bool(settings.value("peaks/follow_cursor", 0, int)))
        layout.addWidget(self.followCursorCheckBox)
        self.roundingCheckBox.toggled.connect(self.options_changed)
        self.roundingDecimalsSpinBox.valueChanged.connect(self.options_changed)
        self.roundingModeComboBox.currentIndexChanged.connect(self.options_changed)
        self.followCursorCheckBox.toggled.connect(self.options_changed)
        self.roundingDecimalsSpinBox.setEnabled(self.roundingCheckBox.isChecked())
        self.roundingModeComboBox.setEnabled(self.roundingCheckBox.isChecked())
        self.bandFloorSpinBox.setToolTip("Width is measured across the contiguous band at or above this level.")

        limit_layout = QtWidgets.QHBoxLayout()
        limit_layout.addWidget(QtWidgets.QLabel("Max rows:"))
        self.limitSpinBox = QtWidgets.QSpinBox()
        self.limitSpinBox.setRange(0, 1000000)
        self.limitSpinBox.setSpecialValueText("All")
        self.limitSpinBox.setValue(settings.value("peaks/max_rows", 0, int))
        self.limit = self.limitSpinBox.value() or None
        self.limitSpinBox.setToolTip("0 = all peaks above Trigger level; otherwise show the strongest N")
        self.limitSpinBox.valueChanged.connect(self.options_changed)
        limit_layout.addWidget(self.limitSpinBox)
        layout.addLayout(limit_layout)
        self.countLabel = QtWidgets.QLabel()
        self.countLabel.setWordWrap(True)
        layout.addWidget(self.countLabel)

        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["#", "Frequency", "Power", "Width"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(2, QtCore.Qt.DescendingOrder)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self.select_table_peak)

        layout.addWidget(self.table)
        self.autoRefreshCheckBox.toggled.connect(self.update_count_label)
        self.update_count_label()

    def clear(self):
        self.table.setRowCount(0)
        self.last_x = self.last_y = None
        self.total_found = 0
        self.update_count_label()

    def update_count_label(self, *args):
        self.countLabel.setText("Shown: {} / {} found. {}".format(
            self.table.rowCount(), self.total_found,
            "Auto refresh on." if self.autoRefreshCheckBox.isChecked() else "Auto off: press Refresh for current data."))

    def options_changed(self, *args):
        settings = QtCore.QSettings()
        settings.setValue("peaks/rounding", int(self.roundingCheckBox.isChecked()))
        settings.setValue("peaks/rounding_decimals", self.roundingDecimalsSpinBox.value())
        settings.setValue("peaks/rounding_mode", self.roundingModeComboBox.currentData())
        settings.setValue("peaks/follow_cursor", int(self.followCursorCheckBox.isChecked()))
        settings.setValue("peaks/max_rows", self.limitSpinBox.value())
        self.limit = self.limitSpinBox.value() or None
        self.roundingDecimalsSpinBox.setEnabled(self.roundingCheckBox.isChecked())
        self.roundingModeComboBox.setEnabled(self.roundingCheckBox.isChecked())
        self.update_peaks(self.last_x, self.last_y)

    def frequency_text(self, frequency):
        decimals = 6
        rounded = frequency
        if self.roundingCheckBox.isChecked():
            decimals = self.roundingDecimalsSpinBox.value()
            rounded = round_frequencies([frequency], decimals, self.roundingModeComboBox.currentData())[0]
        return "{:.{}f}".format(rounded / 1e6, decimals), float(rounded)

    def selected_peak(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(QtCore.Qt.UserRole + 1) if item is not None else None

    def select_table_peak(self, row, column=0):
        if not self.followCursorCheckBox.isChecked():
            return
        item = self.table.item(row, 0)
        if item is not None:
            frequency, power = item.data(QtCore.Qt.UserRole + 1)
            self.peak_selected.emit(frequency, power)

    def follow_frequency(self, frequency, power, frequency_tolerance, power_tolerance):
        if not self.followCursorCheckBox.isChecked() or not self.table.rowCount():
            return False
        if frequency_tolerance <= 0 or power_tolerance <= 0:
            return False
        hits = []
        for row in range(self.table.rowCount()):
            peak_frequency, peak_power = self.table.item(row, 0).data(QtCore.Qt.UserRole + 1)
            distance = ((peak_frequency - frequency) / frequency_tolerance) ** 2 + (
                (peak_power - power) / power_tolerance) ** 2
            if distance <= 1:
                hits.append((distance, row))
        if not hits:
            return False
        _, row = min(hits)
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, 0))
        self.select_table_peak(row)
        return True

    def update_peaks(self, x, y):
        self.last_x = np.array(x, copy=True) if x is not None else None
        self.last_y = np.array(y, copy=True) if y is not None else None
        selected = self.selected_peak()
        bands_mode = self.signalBandsCheckBox.isChecked()
        bands = find_signal_bands(x, y, len(x) if x is not None else 0,
                                  self.bandFloorSpinBox.value())
        if bands_mode:
            records = [(start, stop, frequency, power) for start, stop, frequency, power in bands
                       if power >= self.minPowerSpinBox.value()]
            self.total_found = len(records)
            records = records[:self.limit]
            headers = ["#", "Signal band", "Peak", "Power", "Width"]
        else:
            records = []
            peaks = find_peak_frequencies(x, y, min_power=self.minPowerSpinBox.value())
            self.total_found = len(peaks)
            # Find containing bands without scanning every band for every peak.
            by_frequency = sorted(bands, key=lambda band: band[0])
            starts = np.array([band[0] for band in by_frequency])
            for frequency, power in peaks[:self.limit]:
                position = int(np.searchsorted(starts, frequency, side='right')) - 1
                band = by_frequency[position] if position >= 0 else None
                if band is not None and frequency > band[1]:
                    band = None
                records.append((band[0] if band else None, band[1] if band else None, frequency, power))
            headers = ["#", "Frequency", "Power", "Width"]
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        # Follow the column name when switching between peaks and bands.
        old_header = self.table.horizontalHeaderItem(sort_column)
        if old_header is not None and old_header.text() in headers:
            sort_column = headers.index(old_header.text())
        else:
            sort_column = headers.index("Power")
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(records))
        for row, (start, stop, frequency, power) in enumerate(records):
            freq_text, rounded_frequency = self.frequency_text(frequency)
            values = [(str(row + 1), row + 1)]
            if bands_mode:
                start_text, rounded_start = self.frequency_text(start)
                stop_text, _ = self.frequency_text(stop)
                values.append(("{}–{} MHz".format(start_text, stop_text), rounded_start))
            values.extend([(freq_text + " MHz", rounded_frequency), ("{:.3f} dB".format(power), power)])
            width = stop - start if start is not None else None
            values.append(("{:.3f} kHz".format(width / 1000) if width is not None else "—",
                           width if width is not None else -1))
            for column, (text, numeric_value) in enumerate(values):
                item = NumericTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, numeric_value)
                item.setToolTip("Peak: {:.9f} MHz, {:.3f} dB".format(frequency / 1e6, power))
                self.table.setItem(row, column, item)
            self.table.item(row, 0).setData(QtCore.Qt.UserRole + 1, (frequency, power))
            self.table.item(row, len(headers) - 1).setToolTip(
                "Width before rounding, at Band floor. Peaks in one continuous band share its width. "
                "A dash means the peak is below Band floor.")
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(sort_column, sort_order)
        self.update_count_label()
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        if selected is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(QtCore.Qt.UserRole + 1)[0] == selected[0]:
                    self.table.selectRow(row)
                    self.select_table_peak(row)
                    break

    def update_signal_bands(self, x, y):
        self.update_peaks(x, y)
