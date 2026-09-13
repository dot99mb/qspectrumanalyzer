"""Offline spectrum snapshot comparison and frequency-linked viewing."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from Qt import QtCore, QtWidgets

from .snapshots import read_snapshot


def cursor_value(x, y, frequency):
    """Interpolate only neighbouring samples, without converting whole arrays."""
    index = int(np.searchsorted(x, frequency))
    if index < len(x) and x[index] == frequency:
        return float(y[index])
    if index == 0 or index == len(x):
        return np.nan
    x0, x1 = float(x[index - 1]), float(x[index])
    y0, y1 = float(y[index - 1]), float(y[index])
    if x1 <= x0:
        return np.nan
    return y0 + (y1 - y0) * ((frequency - x0) / (x1 - x0))


def compare_peaks(snapshot, threshold):
    if snapshot.get('kind', 'spectrum') != 'spectrum':
        raise ValueError('Для сравнения выберите слепок спектра.')
    curves = {curve['name']: curve for curve in snapshot['curves']}
    if 'Max hold' not in curves or 'Average' not in curves:
        raise ValueError('Слепок должен содержать Max hold и Average. Включите обе кривые на ПК и создайте слепок.')
    maximum, average = curves['Max hold'], curves['Average']
    x, y = np.asarray(maximum['x']), np.asarray(maximum['y'])
    ax, ay = np.asarray(average['x']), np.asarray(average['y'])
    if np.any(np.diff(x) <= 0) or np.any(np.diff(ax) <= 0):
        raise ValueError('Частоты кривых должны строго возрастать.')
    # Include edge maxima and choose the first point of a flat peak.
    finite = np.isfinite(y)
    left = np.r_[-np.inf, y[:-1]]
    right = np.r_[y[1:], -np.inf]
    indices = np.flatnonzero(finite & (y > left) & (y >= right) & (y >= threshold))
    if len(y) < 2:
        indices = np.array([], dtype=int)
    frequencies, peaks = x[indices], y[indices]
    averages = np.interp(frequencies, ax, ay, left=np.nan, right=np.nan)
    delta = peaks - averages
    with np.errstate(over='ignore', invalid='ignore'):
        ratio = 100. * np.power(10., (averages - peaks) / 10.)
    return np.column_stack((frequencies / 1e6, peaks, averages, delta, ratio))


def catalog(directory):
    entries = []
    if not Path(directory).is_dir():
        raise ValueError('Выберите существующий каталог слепков.')
    skipped = 0
    for path in sorted(Path(directory).glob('spectrum_*.npz'), reverse=True):
        try:
            info = read_snapshot(path, metadata_only=True)
            bounds = info.get('frequency_range', info['view_range'][0])
            entries.append((path, info.get('name') or path.stem, info['created'],
                            info.get('kind', 'spectrum'), bounds))
        except Exception:
            skipped += 1
    return entries, skipped


def load_snapshot(path):
    snapshot = read_snapshot(path)
    if snapshot.get('kind') == 'waterfall':
        # Reduce in the worker before handing the image to Qt.
        history = snapshot.pop('history')
        edges = np.linspace(0, history.shape[1], min(1600, history.shape[1]) + 1, dtype=int)
        power = np.add.reduceat(history, edges[:-1], axis=1, dtype=float) / np.diff(edges)
        low, high = snapshot['levels']
        values = (power - low) / (high - low) * 255 if high > low else np.zeros_like(power)
        snapshot['pixels'] = np.clip(np.nan_to_num(values, nan=0., posinf=255., neginf=0.), 0, 255).astype(np.uint8)
        del snapshot['frequencies']
    return snapshot


class ComparisonModel(QtCore.QAbstractTableModel):
    headers = ['Частота, МГц', 'Max hold, dB', 'Average, dB', 'Max − Average, dB', 'Average / Max, %']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = np.empty((0, 5))
        self.column, self.order = 0, QtCore.Qt.AscendingOrder

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else 5

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if index.isValid() and role == QtCore.Qt.DisplayRole:
            value = self.rows[index.row(), index.column()]
            return ('{:.6f}' if index.column() == 0 else '{:.3f}').format(value) if np.isfinite(value) else '—'
        if role == QtCore.Qt.TextAlignmentRole:
            return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            return self.headers[section] if orientation == QtCore.Qt.Horizontal else str(section + 1)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = np.asarray(rows).reshape((-1, 5))
        self.endResetModel()
        self.sort(self.column, self.order)

    def sort(self, column, order=QtCore.Qt.AscendingOrder):
        self.column, self.order = column, order
        self.beginResetModel()
        values = self.rows[:, column]
        keys = values if order == QtCore.Qt.AscendingOrder else -values
        self.rows = self.rows[np.argsort(np.where(np.isfinite(keys), keys, np.inf), kind='stable')]
        self.endResetModel()


class SnapshotAnalysisWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle('Анализ слепков спектра')
        self.resize(1200, 800)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='snapshot-analysis')
        self.pending = {}
        self.spectrum = self.waterfall = None
        self.spectrum_path = self.waterfall_path = None
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        viewer = QtWidgets.QWidget()
        self.tabs.addTab(viewer, 'Просмотр слепков')
        layout = QtWidgets.QVBoxLayout(viewer)
        controls = QtWidgets.QHBoxLayout()
        self.directory = QtWidgets.QLineEdit(QtCore.QSettings().value('snapshots/directory', str(Path.home())))
        choose = QtWidgets.QPushButton('Каталог…')
        refresh = QtWidgets.QPushButton('Обновить')
        controls.addWidget(self.directory)
        controls.addWidget(choose)
        controls.addWidget(refresh)
        controls.addWidget(QtWidgets.QLabel('Масштаб спектра:'))
        self.zoom_mode = QtWidgets.QComboBox()
        self.zoom_mode.addItems(['Частота', 'Уровень', 'Обе оси'])
        controls.addWidget(self.zoom_mode)
        self.reset_zoom = QtWidgets.QPushButton('Весь диапазон')
        controls.addWidget(self.reset_zoom)
        layout.addLayout(controls)
        split = QtWidgets.QSplitter()
        self.splitter = split
        layout.addWidget(split, 1)
        self.files = QtWidgets.QTableWidget(0, 4)
        self.files.setHorizontalHeaderLabels(['Имя', 'Тип', 'Создан', 'Диапазон, МГц'])
        self.files.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.files.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.files.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        split.addWidget(self.files)
        graphics = pg.GraphicsLayoutWidget()
        self.graphics = graphics
        graphics.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        split.addWidget(graphics)
        split.setSizes([360, 840])
        self.spectrum_coordinates = graphics.addLabel(row=0, col=0, justify='right')
        self.plot = graphics.addPlot(row=1, col=0)
        self.legend = self.plot.addLegend()
        self.plot.setLabel('left', 'Уровень', units='dB')
        self.waterfall_coordinates = graphics.addLabel(row=2, col=0, justify='right')
        self.waterfall_plot = graphics.addPlot(row=3, col=0)
        self.waterfall_plot.setLabel('left', 'Проходы')
        for plot in (self.plot, self.waterfall_plot):
            plot.setLabel('bottom', 'Частота', units='Hz')
            plot.getAxis('left').setWidth(80)
            plot.showGrid(x=True, y=True)
        self.waterfall_plot.setXLink(self.plot)
        for plot in (self.plot, self.waterfall_plot):
            plot.enableAutoRange(x=False, y=False)
            plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
            plot.hideButtons()
        self.waterfall_plot.setMouseEnabled(x=True, y=False)
        graphics.ci.layout.setRowFixedHeight(0, 28)
        graphics.ci.layout.setRowFixedHeight(2, 28)
        graphics.ci.layout.setRowStretchFactor(1, 1)
        graphics.ci.layout.setRowStretchFactor(3, 1)
        self.zoom_mode.currentIndexChanged.connect(self.set_zoom_mode)
        self.reset_zoom.clicked.connect(self.fit_view)
        self.set_zoom_mode()
        self.image = pg.ImageItem(axisOrder='col-major', autoDownsample=True)
        self.waterfall_plot.addItem(self.image)
        self.image.hide()
        self.cursor_frequency = None
        self.cursor_lines = []
        for target, angle in ((self.plot, 90), (self.plot, 0),
                              (self.waterfall_plot, 90), (self.waterfall_plot, 0)):
            line = pg.InfiniteLine(angle=angle, movable=False, pen=pg.mkPen('#ffff00', width=1))
            line.setZValue(1000)
            target.addItem(line, ignoreBounds=True)
            line.hide()
            self.cursor_lines.append(line)
        self.reset_cursor()
        self.mouse_proxy = pg.SignalProxy(graphics.scene().sigMouseMoved, rateLimit=120, delay=1/120, threadSafe=False, slot=self.mouse_moved)
        graphics.scene().sigMouseClicked.connect(self.mouse_clicked)
        self.viewer_status = QtWidgets.QLabel('Выберите спектр и, при необходимости, водопад из списка слева.')
        self.viewer_status.setWordWrap(True)
        self.viewer_status.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self.viewer_status)
        comparison = QtWidgets.QWidget()
        self.tabs.addTab(comparison, 'Сравнение пиков')
        layout = QtWidgets.QVBoxLayout(comparison)
        self.selected_label = QtWidgets.QLabel('Спектр не выбран')
        layout.addWidget(self.selected_label)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Минимальный уровень пика, dB:'))
        self.threshold = QtWidgets.QDoubleSpinBox()
        self.threshold.setRange(-300, 100)
        self.threshold.setDecimals(2)
        self.threshold.setValue(QtCore.QSettings().value('snapshot_analysis/threshold', -80., float))
        row.addWidget(self.threshold)
        row.addStretch()
        layout.addLayout(row)
        note = QtWidgets.QLabel('Пики Max hold сравниваются с Average на той же частоте.\n'
                               'Average / Max, % = 100 × 10^((Average − Max) / 10). '
                               'При разных сетках Average интерполируется; вне её диапазона — нет данных.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.comparison_status = QtWidgets.QLabel()
        layout.addWidget(self.comparison_status)
        self.model = ComparisonModel(self)
        self.table = QtWidgets.QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        self.debounce = QtCore.QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(200)
        self.debounce.timeout.connect(self.compare)
        self.threshold.valueChanged.connect(self.threshold_changed)
        choose.clicked.connect(self.choose_directory)
        refresh.clicked.connect(self.refresh)
        self.directory.returnPressed.connect(self.refresh)
        self.files.itemSelectionChanged.connect(self.select_file)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        self.refresh()

    def clear_spectrum_plot(self):
        self.plot.clear()
        for line in self.cursor_lines[:2]:
            self.plot.addItem(line, ignoreBounds=True)
        self.reset_cursor()

    def reset_cursor(self):
        self.cursor_frequency = None
        for line in self.cursor_lines:
            line.hide()
        self.spectrum_coordinates.setText('f=— MHz, P=— dB', size='12pt')
        self.waterfall_coordinates.setText('f=— MHz, проход=—', size='12pt')

    def set_cursor(self, frequency, power=None, sweep=None):
        if self.spectrum is None:
            return
        self.cursor_frequency = frequency
        signal_power = None
        for curve in self.spectrum['curves']:
            if curve['name'] not in ('Max hold', 'Average', 'Main'):
                continue
            value = cursor_value(curve['x'], curve['y'], frequency)
            if np.isfinite(value):
                if signal_power is None or curve['name'] == 'Max hold':
                    signal_power = value
        vertical, horizontal, wf_vertical, wf_horizontal = self.cursor_lines
        vertical.setPos(frequency)
        vertical.show()
        level = power if power is not None else signal_power
        if level is not None and np.isfinite(level):
            horizontal.setPos(level)
            horizontal.show()
        else:
            horizontal.hide()
        if self.image.isVisible():
            wf_vertical.setPos(frequency)
            wf_vertical.show()
            if sweep is not None:
                wf_horizontal.setPos(sweep)
                wf_horizontal.show()
        else:
            wf_vertical.hide()
            wf_horizontal.hide()
        level_text = '{:.3f}'.format(level) if level is not None and np.isfinite(level) else '—'
        self.spectrum_coordinates.setText(
            'f={:.6f} MHz, P={} dB'.format(frequency / 1e6, level_text), size='12pt')
        if self.image.isVisible():
            row_text = '{:.2f}'.format(wf_horizontal.value()) if wf_horizontal.isVisible() else '—'
            self.waterfall_coordinates.setText(
                'f={:.6f} MHz, проход={}'.format(frequency / 1e6, row_text), size='12pt')
        else:
            self.waterfall_coordinates.setText('f=— MHz, проход=—', size='12pt')

    def mouse_moved(self, event):
        pos = event[0]
        if self.plot.vb.sceneBoundingRect().contains(pos):
            point = self.plot.vb.mapSceneToView(pos)
            self.set_cursor(point.x(), power=point.y())
        elif self.image.isVisible() and self.waterfall_plot.vb.sceneBoundingRect().contains(pos):
            point = self.waterfall_plot.vb.mapSceneToView(pos)
            self.set_cursor(point.x(), sweep=point.y())

    def mouse_clicked(self, event):
        if (self.spectrum is None or event.button() != QtCore.Qt.LeftButton
                or not self.plot.vb.sceneBoundingRect().contains(event.scenePos())):
            return
        point = self.plot.vb.mapSceneToView(event.scenePos())
        dx, dy = (abs(v) * 8 for v in self.plot.vb.viewPixelSize())
        if dx <= 0 or dy <= 0:
            return
        candidates = []
        for curve in self.spectrum['curves']:
            x, y = curve['x'], curve['y']
            begin = max(1, np.searchsorted(x, point.x() - dx))
            end = min(len(x) - 1, np.searchsorted(x, point.x() + dx, side='right'))
            for i in range(begin, end):
                if np.isfinite(y[i]) and y[i] > y[i-1] and y[i] >= y[i+1] and abs(y[i] - point.y()) <= dy:
                    distance = ((x[i] - point.x()) / dx)**2 + ((y[i] - point.y()) / dy)**2
                    candidates.append((distance, x[i], y[i]))
        if candidates:
            _, frequency, power = min(candidates)
            self.set_cursor(frequency, power=power)
            event.accept()

    def set_zoom_mode(self, *args):
        mode = self.zoom_mode.currentIndex()
        self.plot.setMouseEnabled(x=mode != 1, y=mode != 0)

    def fit_view(self):
        if self.spectrum is None:
            return
        bounds = self.spectrum['view_range']
        for plot in (self.plot, self.waterfall_plot):
            plot.setLimits(xMin=bounds[0][0], xMax=bounds[0][1],
                           minXRange=min(1., bounds[0][1] - bounds[0][0]),
                           maxXRange=bounds[0][1] - bounds[0][0])
        self.plot.setRange(xRange=bounds[0], yRange=bounds[1], padding=0)
        if self.waterfall is not None and self.image.isVisible():
            self.waterfall_plot.setYRange(-len(self.waterfall['pixels']), 0, padding=0)

    def submit(self, key, function, *args):
        old = self.pending.pop(key, None)
        if old:
            old.cancel()
        self.pending[key] = self.executor.submit(function, *args)

    def choose_directory(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, 'Каталог слепков', self.directory.text())
        if directory:
            self.directory.setText(directory)
            self.refresh()

    def refresh(self):
        for future in self.pending.values():
            future.cancel()
        self.pending.clear()
        self.spectrum = self.waterfall = None
        self.clear_spectrum_plot()
        self.legend.clear()
        self.image.hide()
        self.files.setRowCount(0)
        self.model.set_rows([])
        self.selected_label.setText('Спектр не выбран')
        self.comparison_status.clear()
        self.viewer_status.setText('Чтение списка слепков…')
        self.submit('catalog', catalog, self.directory.text())

    def select_file(self):
        row = self.files.currentRow()
        item = self.files.item(row, 0)
        if not item:
            return
        path, kind = item.data(QtCore.Qt.UserRole)
        if kind == 'spectrum':
            self.spectrum = None
            self.clear_spectrum_plot()
            self.legend.clear()
            self.image.hide()
            self.comparison_status.setText('Загрузка спектра…')
            self.model.set_rows([])
            previous = self.pending.pop('compare', None)
            if previous:
                previous.cancel()
            self.spectrum_path = path
            self.selected_label.setText('Загрузка: ' + path.name)
        else:
            self.waterfall = None
            self.image.hide()
            for line in self.cursor_lines[2:]:
                line.hide()
            self.waterfall_path = path
        self.viewer_status.setText('Загрузка слепка…')
        self.submit(kind, load_snapshot, path)

    def threshold_changed(self):
        self.model.set_rows([])
        old = self.pending.pop('compare', None)
        if old:
            old.cancel()
        self.debounce.start()

    def compare(self):
        QtCore.QSettings().setValue('snapshot_analysis/threshold', self.threshold.value())
        if self.spectrum is not None:
            self.comparison_status.setText('Поиск пиков…')
            self.submit('compare', compare_peaks, self.spectrum, self.threshold.value())

    def display_waterfall(self):
        self.image.hide()
        for line in self.cursor_lines[2:]:
            line.hide()
        self.waterfall_coordinates.setText('f=— MHz, проход=—', size='12pt')
        if self.waterfall is None:
            return
        if self.spectrum is None:
            self.viewer_status.setText('Выберите спектр для сопоставления с водопадом.')
            return
        bounds = self.waterfall['frequency_range']
        if not np.allclose(bounds, self.spectrum['view_range'][0], rtol=0, atol=1):
            self.viewer_status.setText('Диапазоны спектра и водопада не совпадают. Водопад скрыт.')
            return
        pixels = self.waterfall['pixels']
        self.image.setLookupTable(self.waterfall['lut'])
        self.image.setImage(pixels.T, levels=[0, 255], autoLevels=False)
        self.image.setRect(QtCore.QRectF(bounds[0], -len(pixels), bounds[1] - bounds[0], len(pixels)))
        self.image.show()
        self.waterfall_plot.setLimits(yMin=-len(pixels), yMax=0, minYRange=len(pixels), maxYRange=len(pixels))
        self.waterfall_plot.setYRange(-len(pixels), 0, padding=0)
        self.waterfall_plot.setTitle(self.waterfall.get('name') or self.waterfall_path.name)
        self.viewer_status.setText('Спектр и водопад: общий масштаб частот.')
        if self.cursor_frequency is not None:
            self.set_cursor(self.cursor_frequency)

    def poll(self):
        for key, future in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[key]
            try:
                result = future.result()
                if key == 'catalog':
                    entries, skipped = result
                    self.files.setRowCount(len(entries))
                    for row, (path, name, created, kind, bounds) in enumerate(entries):
                        for col, text in enumerate((name, 'Спектр' if kind == 'spectrum' else 'Водопад',
                                                    created, '{:.3f}–{:.3f}'.format(bounds[0]/1e6, bounds[1]/1e6))):
                            item = QtWidgets.QTableWidgetItem(text)
                            item.setData(QtCore.Qt.UserRole, (path, kind))
                            self.files.setItem(row, col, item)
                    self.viewer_status.setText('Слепков: {}. Пропущено повреждённых: {}.'.format(len(entries), skipped))
                elif key == 'compare':
                    self.model.set_rows(result)
                    self.comparison_status.setText('Найдено пиков: {}'.format(len(result)))
                elif key == 'spectrum':
                    self.spectrum = result
                    self.clear_spectrum_plot()
                    self.legend.clear()
                    for curve in result['curves']:
                        item = self.plot.plot(curve['x'], curve['y'], pen=pg.mkPen(curve['color']), name=curve['name'],
                                              autoDownsample=True, downsampleMethod='peak')
                        # Cursor repaint must reuse the spectrum raster. Qt
                        # invalidates this cache when the view transform changes.
                        item.curve.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
                    self.fit_view()
                    title = result.get('name') or self.spectrum_path.name
                    self.plot.setTitle(title)
                    self.selected_label.setText('Спектр: ' + title)
                    self.viewer_status.setText('Спектр загружен.')
                    self.compare()
                    self.display_waterfall()
                else:
                    self.waterfall = result
                    self.display_waterfall()
            except Exception as error:
                if key == 'compare':
                    self.comparison_status.setText(str(error))
                else:
                    self.viewer_status.setText(str(error))
                    if key == 'spectrum':
                        self.comparison_status.setText(str(error))
                    if key == 'waterfall':
                        self.waterfall = None
                        self.image.hide()

    def closeEvent(self, event):
        self.timer.stop()
        self.mouse_proxy.disconnect()
        self.debounce.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
