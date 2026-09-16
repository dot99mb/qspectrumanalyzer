"""Detection of new persistent bands against a frozen waterfall background."""
from collections import deque
from datetime import datetime

import numpy as np
from Qt import QtCore, QtWidgets
from .peaks import NumericTableWidgetItem


class SignalDetector:
    def __init__(self, x, history, minimum=-80., excess=8., seconds=10., presence=.8, tolerance=20000.):
        self.x = np.asarray(x).copy()
        if len(history) < 3:
            raise ValueError('Для фона нужны минимум три прохода водопада.')
        self.background = np.empty(len(self.x))
        self.known = np.empty(len(self.x), dtype=bool)
        for begin in range(0, len(self.x), 4096):
            block = np.asarray(history)[:, begin:begin+4096]
            self.background[begin:begin+4096] = np.nanmedian(block, axis=0)
            self.known[begin:begin+4096] = np.mean(block >= minimum, axis=0) >= .2
        prefix = np.r_[0, np.cumsum(self.known)]
        low = np.searchsorted(self.x, self.x-tolerance)
        high = np.searchsorted(self.x, self.x+tolerance, side='right')
        self.known = prefix[high] - prefix[low] > 0
        self.step = float(np.median(np.diff(self.x))) if len(self.x) > 1 else 0.
        self.minimum, self.excess = minimum, excess
        self.seconds, self.presence, self.tolerance = seconds, presence, tolerance
        self.sweeps = deque()
        self.tracks = []
        self.sequence = 0

    def update(self, x, y, wall, mono):
        if not np.array_equal(x, self.x):
            raise ValueError('Сетка частот изменилась. Зафиксируйте фон заново.')
        if self.sweeps and mono <= self.sweeps[-1]:
            raise ValueError('Нарушен порядок временных меток. Зафиксируйте фон заново.')
        gap = self.sweeps and mono - self.sweeps[-1] > self.seconds
        if gap:
            self.sweeps.clear()
            for track in self.tracks:
                track['hits'].clear()
                track['active'] = False
                track['segment'] = None
        self.sweeps.append(mono)
        while self.sweeps and self.sweeps[0] < mono - self.seconds:
            self.sweeps.popleft()
        mask = (np.isfinite(y) & np.isfinite(self.background) & ~self.known
                & (y >= self.minimum) & (y - self.background >= self.excess))
        indices = np.flatnonzero(mask)
        bands = []
        if len(indices):
            # Adjacent bins and small frequency gaps represent a single band.
            splits = np.flatnonzero((np.diff(indices) > 1) &
                                   (np.diff(self.x[indices]) > self.tolerance)) + 1
            for group in np.split(indices, splits):
                i = group[np.argmax(y[group])]
                bands.append((self.x[group[0]], self.x[group[-1]], self.x[i], float(y[i]),
                              float(y[i] - self.background[i])))
        matched = set()
        for low, high, frequency, power, excess in bands:
            candidates = [t for t in self.tracks if t['id'] not in matched and
                          mono - t['last_mono'] <= self.seconds and
                          low <= t['high'] + self.tolerance and high >= t['low'] - self.tolerance]
            if candidates:
                track = min(candidates, key=lambda t: abs(t['frequency'] - frequency))
            else:
                self.sequence += 1
                track = dict(id=self.sequence, first=wall, segment=mono, last_mono=mono,
                             hits=deque(), maximum=power, total=0., count=0,
                             confirmed=False, active=False)
                self.tracks.append(track)
            if track['segment'] is None:
                track['segment'] = mono
            track.update(low=low, high=high, frequency=frequency, last=wall,
                         last_mono=mono, excess=excess)
            track['hits'].append(mono)
            track['maximum'] = max(track['maximum'], power)
            track['total'] += power
            track['count'] += 1
            matched.add(track['id'])
        for track in self.tracks:
            while track['hits'] and track['hits'][0] < mono - self.seconds:
                track['hits'].popleft()
            if not track['hits']:
                track['segment'] = None
            track['occupancy'] = len(track['hits']) / len(self.sweeps)
            stable = (track['segment'] is not None and mono - track['segment'] >= self.seconds
                      and len(self.sweeps) >= 3 and track['occupancy'] >= self.presence)
            track['confirmed'] |= stable
            track['active'] = bool(stable and mono - track['last_mono'] <= self.seconds)
        self.tracks = [t for t in self.tracks if t['confirmed'] or mono - t['last_mono'] <= self.seconds]
        # Preserve the latest 2000 tracks, rather than grow memory indefinitely.
        if len(self.tracks) > 2000:
            self.tracks = sorted(self.tracks, key=lambda t: t['last_mono'])[-2000:]
        rows = []
        step = self.step
        for t in self.tracks:
            if t['confirmed']:
                rows.append((t['id'], t['frequency']/1e6, (t['high']-t['low']+step)/1e3,
                             t['first'], t['last'], t['last']-t['first'],
                             t['total']/t['count'], t['maximum'], t['excess'],
                             t['occupancy']*100, t['active'], t['low'], t['high']))
        return rows


class SignalDetectionWidget(QtWidgets.QWidget):
    start_requested = QtCore.Signal(object)
    stop_requested = QtCore.Signal()
    bands_changed = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.fields = {}
        form = QtWidgets.QFormLayout()
        for key, label, value, low, high in [
            ('minimum', 'Минимум, dB:', -80, -300, 100),
            ('excess', 'Превышение фона, dB:', 8, .1, 100),
            ('seconds', 'Подтверждение, с:', 10, 1, 3600),
            ('presence', 'Присутствие, %:', 80, 1, 100),
            ('tolerance', 'Допуск частоты, кГц:', 20, 0, 10000),
        ]:
            field = QtWidgets.QDoubleSpinBox()
            field.setRange(low, high)
            field.setValue(QtCore.QSettings().value('new_signals/'+key, value, float))
            self.fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        self.start_button = QtWidgets.QPushButton('Зафиксировать фон и начать')
        self.stop_button = QtWidgets.QPushButton('Остановить анализ')
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        self.status = QtWidgets.QLabel('Накопите фон без новых сигналов. Анализ использует данные водопада до сглаживания.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(['№', 'Частота, МГц', 'Ширина, кГц', 'Появление',
            'Последний', 'Длительность, с', 'Средний, dB', 'Максимум, dB', 'Выше фона, dB',
            'Присутствие, %', 'Состояние'])
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit())
        self.active = False

    def start(self):
        config = {key: field.value() for key, field in self.fields.items()}
        for key, value in config.items():
            QtCore.QSettings().setValue('new_signals/'+key, value)
        config['presence'] /= 100
        config['tolerance'] *= 1000
        self.start_requested.emit(config)

    def result(self, result):
        message, rows, active = result
        self.active = active
        self.status.setText(message)
        for field in self.fields.values():
            field.setEnabled(not active)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values[:11]):
                if col in (3, 4):
                    text = datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
                elif col == 10:
                    text = 'Устойчивый' if value else 'Пропал / неустойчивый'
                else:
                    text = str(value) if col == 0 else '{:.3f}'.format(value)
                item = NumericTableWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, value)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.bands_changed.emit([(r[11], r[12]) for r in rows if r[10]][:100])
