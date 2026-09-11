"""Spectrum snapshots: a PNG preview paired with an NPZ of plotted curves."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid
from zipfile import BadZipFile, ZipFile
import io
import shutil

import numpy as np
from Qt import QtCore, QtGui, QtWidgets


FORMAT = 'qspectrumanalyzer-snapshot-v1'


def rename_snapshot(path, name):
    """Replace metadata atomically, retaining the image and curve arrays."""
    path = Path(path)
    metadata = read_snapshot(path, metadata_only=True)
    metadata['name'] = name.strip()
    buffer = io.BytesIO()
    np.save(buffer, np.array(json.dumps(metadata, ensure_ascii=False)), allow_pickle=False)
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', suffix='.npz', dir=str(path.parent))
    os.close(fd)
    try:
        with ZipFile(path) as source, ZipFile(temporary, 'w') as target:
            for entry in source.infolist():
                if entry.filename == 'metadata.npy':
                    target.writestr(entry, buffer.getvalue())
                else:
                    with source.open(entry) as incoming, target.open(entry, 'w', force_zip64=True) as outgoing:
                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_snapshot(path, metadata_only=False):
    with open(path, 'rb') as stream, np.load(stream, allow_pickle=False) as archive:
        metadata = json.loads(str(archive['metadata'].item()))
        if not isinstance(metadata, dict) or metadata.get('format') != FORMAT:
            raise ValueError('Unsupported snapshot format')
        ranges = np.asarray(metadata['view_range'], dtype=float)
        if ranges.shape != (2, 2) or not np.isfinite(ranges).all() or np.any(ranges[:, 1] <= ranges[:, 0]):
            raise ValueError('Invalid snapshot view range')
        kind = metadata.get('kind', 'spectrum')
        if kind not in ('spectrum', 'waterfall'):
            raise ValueError('Unknown snapshot type')
        if kind == 'waterfall':
            datetime.fromisoformat(metadata['created'])
            frequency_range = np.asarray(metadata['frequency_range'], dtype=float)
            levels = np.asarray(metadata['levels'], dtype=float)
            if frequency_range.shape != (2,) or not np.isfinite(frequency_range).all() or frequency_range[1] <= frequency_range[0]:
                raise ValueError('Invalid waterfall frequency range')
            if levels.shape != (2,) or not np.isfinite(levels).all() or levels[1] < levels[0]:
                raise ValueError('Invalid waterfall levels')
            if metadata_only:
                return metadata
            x, history, lut = archive['frequencies'], archive['history'], archive['lut']
            if x.ndim != 1 or not x.size or not np.isfinite(x).all() or history.ndim != 2 or history.shape[1] != x.size or history.shape[0] < 1:
                raise ValueError('Invalid waterfall data dimensions')
            if history.dtype.kind not in 'fiu' or lut.ndim != 2 or lut.shape[1] not in (3, 4) or lut.dtype != np.uint8:
                raise ValueError('Invalid waterfall image data')
            return dict(metadata, frequencies=x, history=history, lut=lut)
        if not isinstance(metadata['created'], str) or not isinstance(metadata['curves'], list) or not metadata['curves']:
            raise ValueError('Invalid snapshot metadata')
        datetime.fromisoformat(metadata['created'])
        for info in metadata['curves']:
            if not isinstance(info, dict) or not isinstance(info.get('name'), str):
                raise ValueError('Invalid snapshot curve metadata')
            color = info.get('color')
            if not isinstance(color, list) or len(color) != 4 or any(type(c) is not int or not 0 <= c <= 255 for c in color):
                raise ValueError('Invalid snapshot curve color')
        if metadata_only:
            return metadata
        curves = []
        for i, info in enumerate(metadata['curves']):
            x, y = archive['x{}'.format(i)], archive['y{}'.format(i)]
            if x.ndim != 1 or y.ndim != 1 or x.shape != y.shape or not x.size:
                raise ValueError('Invalid snapshot curve dimensions')
            if not np.isfinite(x).all():
                raise ValueError('Invalid snapshot frequencies')
            curves.append(dict(info, x=x, y=y))
        ranges = np.asarray(metadata['view_range'], dtype=float)
        if ranges.shape != (2, 2) or not np.isfinite(ranges).all() or np.any(ranges[:, 1] <= ranges[:, 0]):
            raise ValueError('Invalid snapshot view range')
        if not curves:
            raise ValueError('Snapshot contains no curves')
        return dict(metadata, curves=curves)


def save_snapshot(directory, curves, view_range, screenshot, waterfall=None):
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        raise ValueError('Select an existing snapshot directory')
    if waterfall is None and not curves:
        raise ValueError('No visible spectrum data to capture. Start a measurement and enable a curve.')
    if screenshot.isNull():
        raise ValueError('Could not capture the spectrum image')
    now = datetime.now().astimezone()
    stem = 'spectrum_{}_{}'.format(now.strftime('%Y%m%d_%H%M%S_%f'), uuid.uuid4().hex[:8])
    data_path, image_path = directory / (stem + '.npz'), directory / (stem + '.png')
    metadata = dict(format=FORMAT, created=now.isoformat(timespec='milliseconds'),
                    view_range=view_range, curves=[{'name': c['name'], 'color': c['color']} for c in curves])
    arrays = {'metadata': np.array(json.dumps(metadata))}
    for i, curve in enumerate(curves):
        arrays['x{}'.format(i)] = curve['x']
        arrays['y{}'.format(i)] = curve['y']
    if waterfall is not None:
        metadata.update(kind='waterfall', frequency_range=waterfall['frequency_range'],
                        levels=waterfall['levels'])
        arrays.update(frequencies=waterfall['frequencies'], history=waterfall['history'], lut=waterfall['lut'])
        arrays['metadata'] = np.array(json.dumps(metadata))
    temporary_paths = []
    published_image = False
    try:
        for suffix in ('.png', '.npz'):
            fd, name = tempfile.mkstemp(prefix='.snapshot-', suffix=suffix, dir=str(directory))
            os.close(fd)
            temporary_paths.append(Path(name))
        if not screenshot.save(str(temporary_paths[0]), 'PNG'):
            raise OSError('Could not save snapshot screenshot')
        with temporary_paths[1].open('wb') as stream:
            np.savez_compressed(stream, **arrays)
        os.replace(temporary_paths[0], image_path)
        published_image = True
        # Publishing NPZ last makes a completed pair visible to directory scans.
        os.replace(temporary_paths[1], data_path)
    except Exception:
        if published_image:
            image_path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
    return data_path


class SnapshotSaveThread(QtCore.QThread):
    """Compress and write immutable capture data without blocking Qt events."""
    def __init__(self, directory, curves, view_range, image, waterfall=None, parent=None):
        super().__init__(parent)
        self.arguments = (directory, curves, view_range, image, waterfall)
        self.path = None
        self.error = None

    def run(self):
        try:
            self.path = save_snapshot(*self.arguments)
        except Exception as error:
            self.error = str(error)
        finally:
            self.arguments = None


class SnapshotLoadThread(QtCore.QThread):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.snapshot = None
        self.error = None

    def run(self):
        try:
            self.snapshot = read_snapshot(self.path)
            self.snapshot['filename'] = self.path.name
            if self.snapshot.get('kind') == 'waterfall':
                # Prepare colour indices off-thread. Qt then paints a compact
                # uint8 image instead of repeatedly processing a huge float array.
                history = self.snapshot['history']
                low, high = self.snapshot['levels']
                scale = 255. / (high - low) if high > low else 0.
                pixels = np.empty(history.shape, dtype=np.uint8)
                for row in range(history.shape[0]):
                    if self.isInterruptionRequested():
                        self.snapshot = None
                        return
                    values = (history[row] - low) * scale
                    np.nan_to_num(values, copy=False, nan=0., posinf=255., neginf=0.)
                    pixels[row] = np.clip(values, 0, 255).astype(np.uint8)
                self.snapshot['display_image'] = pixels
                del self.snapshot['history']
        except Exception as error:
            self.error = str(error)
            self.snapshot = None


class SnapshotRenameThread(QtCore.QThread):
    def __init__(self, path, name, parent=None):
        super().__init__(parent)
        self.path, self.name = path, name
        self.previous = None
        self.error = None

    def run(self):
        try:
            self.previous = read_snapshot(self.path, metadata_only=True).get('name', '')
            rename_snapshot(self.path, self.name)
        except Exception as error:
            self.error = str(error)


class SnapshotWidget(QtWidgets.QWidget):
    capture_requested = QtCore.Signal()
    display_requested = QtCore.Signal(object)
    loading_finished = QtCore.Signal()
    snapshot_renamed = QtCore.Signal(object, str)
    range_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preview_path = None
        self.load_thread = None
        self.pending_path = None
        self.load_generation = 0
        self.waterfall_visible = True
        self.pending_kind = None
        self.rename_thread = None
        self.rename_queue = {}
        layout = QtWidgets.QVBoxLayout(self)
        directory_row = QtWidgets.QHBoxLayout()
        directory_row.addWidget(QtWidgets.QLabel('Directory:'))
        self.directoryEdit = QtWidgets.QLineEdit(QtCore.QSettings().value('snapshots/directory', str(Path.home())))
        self.directoryButton = QtWidgets.QPushButton('...')
        self.directoryButton.clicked.connect(self.choose_directory)
        self.directoryEdit.editingFinished.connect(self.directory_changed)
        directory_row.addWidget(self.directoryEdit, 1)
        directory_row.addWidget(self.directoryButton)
        layout.addLayout(directory_row)
        self.typeComboBox = QtWidgets.QComboBox()
        self.typeComboBox.addItem('Spectrum', 'spectrum')
        self.typeComboBox.addItem('Waterfall', 'waterfall')
        type_row = QtWidgets.QHBoxLayout()
        type_row.addWidget(QtWidgets.QLabel('Snapshot type:'))
        type_row.addWidget(self.typeComboBox, 1)
        layout.addLayout(type_row)
        buttons = QtWidgets.QHBoxLayout()
        self.captureButton = QtWidgets.QPushButton('Capture snapshot')
        self.captureButton.clicked.connect(lambda: self.capture_requested.emit())
        self.refreshButton = QtWidgets.QPushButton('Refresh list')
        self.refreshButton.clicked.connect(self.refresh_list)
        buttons.addWidget(self.captureButton)
        buttons.addWidget(self.refreshButton)
        layout.addLayout(buttons)
        self.displayCheckBox = QtWidgets.QCheckBox('Display on graph')
        # Snapshot display is opt-in for each application session; never restore it.
        self.displayCheckBox.setChecked(False)
        self.displayCheckBox.setToolTip('Show the selected snapshot over the live spectrum')
        self.displayCheckBox.toggled.connect(self.display_selected)
        layout.addWidget(self.displayCheckBox)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Name', 'Captured', 'Range [MHz]', 'Curves', 'Type'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditKeyPressed)
        self.table.cellDoubleClicked.connect(self.request_snapshot_range)
        self.table.itemChanged.connect(self.rename_item)
        self.table.setMouseTracking(True)
        self.table.cellEntered.connect(self.preview_row)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.previewLabel = QtWidgets.QLabel('Hover over a snapshot to preview it')
        self.previewLabel.setAlignment(QtCore.Qt.AlignCenter)
        self.previewLabel.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.previewLabel.setMinimumHeight(100)
        self.previewLabel.setMaximumHeight(200)
        layout.addWidget(self.previewLabel)
        self.statusLabel = QtWidgets.QLabel()
        self.statusLabel.setTextFormat(QtCore.Qt.PlainText)
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)
        self.refresh_list()

    def error(self, error):
        self.statusLabel.setText(str(error))
        QtWidgets.QMessageBox.warning(self, 'Spectrum snapshot', str(error))

    def choose_directory(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, 'Snapshot directory', self.directoryEdit.text())
        if path:
            self.directoryEdit.setText(path)
            self.directory_changed()

    def directory_changed(self):
        QtCore.QSettings().setValue('snapshots/directory', self.directoryEdit.text())
        self.refresh_list()

    def selected_path(self):
        item = self.table.item(self.table.currentRow(), 0)
        return Path(item.data(QtCore.Qt.UserRole)) if item is not None else None

    def request_snapshot_range(self, row, column):
        item = self.table.item(row, 0)
        if item is not None:
            self.range_requested.emit(item.data(QtCore.Qt.UserRole + 2))

    def rename_item(self, item):
        if item.column() != 0:
            return
        name = item.text().strip()
        path = Path(item.data(QtCore.Qt.UserRole))
        self.rename_queue[path] = name
        self.start_pending_rename()

    def start_pending_rename(self):
        if self.rename_thread is not None or not self.rename_queue:
            return
        path = next(iter(self.rename_queue))
        name = self.rename_queue.pop(path)
        self.rename_thread = SnapshotRenameThread(path, name, self)
        self.rename_thread.finished.connect(self.rename_finished)
        self.statusLabel.setText('Saving snapshot name…')
        self.rename_thread.start()

    @QtCore.Slot()
    def rename_finished(self):
        worker = self.rename_thread
        self.rename_thread = None
        name = worker.name if worker.error is None else worker.previous
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if Path(item.data(QtCore.Qt.UserRole)) == worker.path:
                saved_name = name if name is not None else item.data(QtCore.Qt.UserRole + 1) or ''
                item.setData(QtCore.Qt.UserRole + 1, saved_name)
                if worker.path not in self.rename_queue:
                    item.setText(saved_name)
                break
        self.table.blockSignals(False)
        if worker.error is not None:
            self.error(worker.error)
        else:
            self.statusLabel.setText('Snapshot name saved')
            self.snapshot_renamed.emit(worker.path, worker.name)
        worker.deleteLater()
        self.start_pending_rename()
        self.loading_finished.emit()

    def refresh_list(self, selected=None):
        selected = Path(selected) if selected else self.selected_path()
        directory = Path(self.directoryEdit.text()).expanduser()
        entries, skipped = [], 0
        try:
            if not self.directoryEdit.text().strip() or not directory.is_dir():
                raise ValueError('Select an existing snapshot directory')
            for path in sorted(directory.glob('spectrum_*.npz'), reverse=True):
                try:
                    metadata = read_snapshot(path, metadata_only=True)
                    kind = metadata.get('kind', 'spectrum')
                    start, stop = metadata['frequency_range'] if kind == 'waterfall' else metadata['view_range'][0]
                    entries.append((path, metadata.get('name', ''), metadata['created'], start, stop,
                                    len(metadata['curves']) if kind == 'spectrum' else '—', kind.title()))
                except (OSError, ValueError, KeyError, TypeError, BadZipFile, EOFError):
                    skipped += 1
        except (OSError, ValueError) as error:
            self.statusLabel.setText(str(error))
            entries = []
        else:
            self.statusLabel.setText('{} snapshots{}'.format(len(entries), ' | {} invalid files skipped'.format(skipped) if skipped else ''))
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        selected_row = -1
        for row, (path, name, created, start, stop, count, kind) in enumerate(entries):
            captured = datetime.fromisoformat(created).strftime('%Y-%m-%d %H:%M:%S')
            for col, value in enumerate([name, captured, '{:.3f}–{:.3f}'.format(start / 1e6, stop / 1e6), str(count), kind]):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(str(path))
                if col != 0:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                else:
                    item.setToolTip('F2: edit name. Double-click: apply snapshot frequency range.\n' + str(path))
                    item.setData(QtCore.Qt.UserRole + 1, name)
                self.table.setItem(row, col, item)
            self.table.item(row, 0).setData(QtCore.Qt.UserRole, str(path))
            self.table.item(row, 0).setData(QtCore.Qt.UserRole + 2, (float(start), float(stop)))
            self.table.item(row, 0).setData(QtCore.Qt.UserRole + 3, kind.lower())
            if path == selected:
                selected_row = row
        self.table.setCurrentCell(-1, -1)
        self.table.clearSelection()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, max(140, min(250, self.table.columnWidth(0))))
        self.preview_path = None
        self.previewLabel.clear()
        self.selection_changed()

    def preview_row(self, row, column=0):
        item = self.table.item(row, 0)
        if item is None:
            return
        path = Path(item.data(QtCore.Qt.UserRole)).with_suffix('.png')
        if self.preview_path == path:
            return
        self.preview_path = path
        reader = QtGui.QImageReader(str(path))
        size = reader.size()
        if size.isValid():
            size.scale(max(100, self.previewLabel.width()), min(190, self.previewLabel.height()), QtCore.Qt.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        if image.isNull():
            self.previewLabel.setText('Screenshot unavailable')
        else:
            self.previewLabel.setPixmap(QtGui.QPixmap.fromImage(image))

    def selection_changed(self):
        row = self.table.currentRow()
        if row >= 0:
            self.preview_row(row)
        else:
            self.previewLabel.setText('Hover over a snapshot to preview it')
        self.display_selected()

    def display_selected(self, *args):
        path = self.selected_path()
        item = self.table.item(self.table.currentRow(), 0)
        self.pending_kind = item.data(QtCore.Qt.UserRole + 3) if item is not None else None
        if self.displayCheckBox.isChecked() and self.pending_kind == 'waterfall' and not self.waterfall_visible:
            self.cancel_loading()
            self.statusLabel.setText('Waterfall is hidden')
            return
        self.load_generation += 1
        self.pending_path = path if self.displayCheckBox.isChecked() else None
        if self.pending_path is None:
            self.display_requested.emit(None)
        if self.load_thread is not None:
            self.load_thread.requestInterruption()
        if self.pending_path is not None:
            self.statusLabel.setText('Loading snapshot…')
            self.start_pending_load()
        elif self.load_thread is not None:
            self.statusLabel.setText('Snapshot display cancelled')

    def start_pending_load(self):
        if self.load_thread is not None or self.pending_path is None:
            return
        worker = SnapshotLoadThread(self.pending_path, self)
        worker.generation = self.load_generation
        self.load_thread = worker
        worker.finished.connect(self.load_finished)
        worker.start()

    @QtCore.Slot()
    def load_finished(self):
        worker = self.load_thread
        self.load_thread = None
        current = worker.generation == self.load_generation and self.pending_path == worker.path
        snapshot, error = worker.snapshot, worker.error
        worker.snapshot = None
        worker.deleteLater()
        if current:
            self.pending_path = None
            if error is not None:
                self.error(error)
            elif snapshot is not None:
                item = self.table.item(self.table.currentRow(), 0)
                if item is not None:
                    snapshot['name'] = item.data(QtCore.Qt.UserRole + 1) or ''
                self.statusLabel.setText('Snapshot loaded')
                self.display_requested.emit(snapshot)
        else:
            self.start_pending_load()
        self.loading_finished.emit()

    def cancel_loading(self):
        self.load_generation += 1
        self.pending_path = None
        if self.load_thread is not None:
            self.load_thread.requestInterruption()

    def set_waterfall_visible(self, visible):
        self.waterfall_visible = visible
        if not visible and self.pending_kind == 'waterfall':
            self.cancel_loading()
            self.statusLabel.setText('Waterfall is hidden')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.preview_path is not None:
            path = self.preview_path
            self.preview_path = None
            for row in range(self.table.rowCount()):
                if Path(self.table.item(row, 0).data(QtCore.Qt.UserRole)).with_suffix('.png') == path:
                    self.preview_row(row)
                    break
