import numpy as np
from qspectrumanalyzer.qt import QtCore, QtWidgets


class NumericTableWidgetItem(QtWidgets.QTableWidgetItem):
    """Table item sorting by numeric data when available."""
    def __lt__(self, other):
        value = self.data(QtCore.Qt.UserRole)
        other_value = other.data(QtCore.Qt.UserRole)
        if value is not None and other_value is not None:
            return value < other_value
        return super().__lt__(other)


def find_peak_frequencies(x, y, limit=20, min_power=None):
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


def find_signal_bands(x, y, limit=20, band_floor=None, min_power=None):
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

    def __init__(self, parent=None):
        super().__init__(parent)

        self.limit = 20

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
        self.minPowerLabel.setText("Min power:")
        filter_layout.addWidget(self.minPowerLabel)

        self.minPowerSpinBox = QtWidgets.QDoubleSpinBox(self)
        self.minPowerSpinBox.setDecimals(1)
        self.minPowerSpinBox.setMinimum(-200.0)
        self.minPowerSpinBox.setMaximum(100.0)
        self.minPowerSpinBox.setSingleStep(1.0)
        self.minPowerSpinBox.setValue(-120.0)
        self.minPowerSpinBox.setSuffix(" dB")
        self.minPowerSpinBox.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.minPowerSpinBox.valueChanged.connect(self.min_power_changed)
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

        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["#", "Frequency", "Power"])
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

        layout.addWidget(self.table)

    def clear(self):
        self.table.setRowCount(0)

    def update_peaks(self, x, y):
        if self.signalBandsCheckBox.isChecked():
            self.update_signal_bands(x, y)
            return

        peaks = find_peak_frequencies(x, y, self.limit, self.minPowerSpinBox.value())
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()

        self.table.setSortingEnabled(False)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["#", "Frequency", "Power"])
        self.table.setRowCount(len(peaks))

        for row, (frequency, power) in enumerate(peaks):
            values = (
                (str(row + 1), row + 1),
                ("{:.6f} MHz".format(frequency / 1e6), frequency),
                ("{:.3f} dB".format(power), power),
            )
            for column, (text, numeric_value) in enumerate(values):
                item = NumericTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, numeric_value)
                if column != 1:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.table.setItem(row, column, item)

        self.table.setSortingEnabled(True)
        self.table.sortByColumn(min(sort_column, 2), sort_order)

    def update_signal_bands(self, x, y):
        bands = find_signal_bands(
            x, y, self.limit, self.bandFloorSpinBox.value(), self.minPowerSpinBox.value()
        )
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()

        self.table.setSortingEnabled(False)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["#", "Signal band", "Peak", "Power"])
        self.table.setRowCount(len(bands))

        for row, (start, stop, peak_frequency, peak_power) in enumerate(bands):
            values = (
                (str(row + 1), row + 1),
                ("{:.6f}-{:.6f} MHz".format(start / 1e6, stop / 1e6), start),
                ("{:.6f} MHz".format(peak_frequency / 1e6), peak_frequency),
                ("{:.3f} dB".format(peak_power), peak_power),
            )
            for column, (text, numeric_value) in enumerate(values):
                item = NumericTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, numeric_value)
                if column != 1:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.table.setItem(row, column, item)

        self.table.setSortingEnabled(True)
        self.table.sortByColumn(min(sort_column, 3), sort_order)
