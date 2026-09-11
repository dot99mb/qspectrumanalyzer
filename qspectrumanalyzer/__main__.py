#!/usr/bin/env python

import sys, os, signal, time, argparse
import types
import shlex
import shutil

from Qt import QtCore, QtGui, QtWidgets
import numpy as np

from qspectrumanalyzer import backends
from qspectrumanalyzer.version import __version__
from qspectrumanalyzer.data import DataStorage
from qspectrumanalyzer.plot import SpectrumPlotWidget, WaterfallPlotWidget
from qspectrumanalyzer.utils import str_to_color, human_time

from qspectrumanalyzer.settings import QSpectrumAnalyzerSettings
from qspectrumanalyzer.smoothing import QSpectrumAnalyzerSmoothing
from qspectrumanalyzer.persistence import QSpectrumAnalyzerPersistence
from qspectrumanalyzer.colors import QSpectrumAnalyzerColors
from qspectrumanalyzer.baseline import QSpectrumAnalyzerBaseline
from qspectrumanalyzer.peaks import PeakListWidget
from qspectrumanalyzer.recording import RecordingWidget
from qspectrumanalyzer.snapshots import SnapshotWidget, SnapshotSaveThread

from qspectrumanalyzer.ui_qspectrumanalyzer import Ui_QSpectrumAnalyzerMainWindow

debug = False

# Allow CTRL+C and/or SIGTERM to kill us (PyQt blocks it otherwise)
signal.signal(signal.SIGINT, signal.SIG_DFL)
signal.signal(signal.SIGTERM, signal.SIG_DFL)


class QSpectrumAnalyzerMainWindow(QtWidgets.QMainWindow, Ui_QSpectrumAnalyzerMainWindow):
    """QSpectrumAnalyzer main window"""
    def __init__(self, parent=None):
        # Initialize UI
        super().__init__(parent)
        self.setupUi(self)

        # Set window icon
        icon_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "qspectrumanalyzer.svg")
        self.setWindowIcon(QtGui.QIcon(icon_path))

        # Create progress bar
        self.progressbar = QtWidgets.QProgressBar()
        self.progressbar.setMaximumWidth(250)
        self.progressbar.setVisible(False)
        self.statusbar.addPermanentWidget(self.progressbar)

        # Create plot widgets and update UI
        self.spectrumPlotWidget = SpectrumPlotWidget(self.mainPlotLayout)
        self.waterfallPlotWidget = WaterfallPlotWidget(self.waterfallPlotLayout, self.histogramPlotLayout)
        self.resetLevelsButton = QtWidgets.QPushButton(self.tr("Reset to defaults"))
        self.resetLevelsButton.setToolTip(self.tr("Restore the default palette and automatic signal levels"))
        self.resetLevelsButton.clicked.connect(self.waterfallPlotWidget.reset_levels)
        self.levelsDockWidgetContents.layout().addWidget(self.resetLevelsButton)
        # Linked views align by screen position; equal axis gutters prevent
        # differing labels from shifting the requested frequency boundaries.
        self.spectrumPlotWidget.plot.getAxis("left").setWidth(80)
        self.waterfallPlotWidget.plot.getAxis("left").setWidth(80)

        # Link main spectrum plot to waterfall plot
        self.spectrumPlotWidget.plot.setXLink(self.waterfallPlotWidget.plot)
        self.install_shared_view_all_actions()
        self.create_peaks_dock()
        self.create_recording_dock()
        self.create_snapshots_dock()
        self.spectrumPlotWidget.trigger_level_callback = self.set_trigger_levels
        self.create_view_menu()
        self.analysis_window = None
        self.actionAnalyzeRecording = self.menu_File.addAction(self.tr("Analyze recording..."))
        self.actionAnalyzeRecording.triggered.connect(self.open_recording_analysis)

        # Setup power thread and connect signals
        self.update_status_timer = QtCore.QTimer()
        self.update_status_timer.timeout.connect(self.update_status)
        self.prev_sweep_time = None
        self.prev_data_timestamp = None
        self.start_timestamp = None
        self.data_storage = None
        self.power_thread = None
        self.backend = None
        self.peak_update_dirty = False
        self.setup_power_thread()

        self.update_buttons()
        self.load_settings()

    def create_peaks_dock(self):
        """Create dock listing average spectrum peak frequencies."""
        self.peaksDockWidget = QtWidgets.QDockWidget(self.tr("Peaks"), self)
        self.peaksDockWidget.setObjectName("peaksDockWidget")
        self.peaksDockWidget.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFloatable |
            QtWidgets.QDockWidget.DockWidgetMovable
        )

        self.peakListWidget = PeakListWidget(self.peaksDockWidget)
        self.peakListWidget.peak_selected.connect(self.spectrumPlotWidget.set_cursor)
        self.spectrumPlotWidget.peak_cursor_callback = self.peakListWidget.follow_frequency
        self.peakListWidget.refresh_requested.connect(self.refresh_peak_frequencies)
        self.peakListWidget.auto_refresh_toggled.connect(self.set_peak_auto_refresh)
        self.peakListWidget.refresh_interval_changed.connect(self.set_peak_refresh_interval)
        self.peakListWidget.source_changed.connect(self.set_peak_source)
        self.peakListWidget.min_power_changed.connect(self.set_peak_min_power)
        self.peakListWidget.band_floor_changed.connect(self.set_peak_band_floor)
        self.peakListWidget.bands_toggled.connect(self.set_peak_bands_enabled)
        self.peaksDockWidget.setWidget(self.peakListWidget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea(2), self.peaksDockWidget)

        self.peak_update_timer = QtCore.QTimer(self)
        self.peak_update_timer.timeout.connect(self.refresh_peak_frequencies_if_dirty)
        self.set_peak_refresh_interval(self.peakListWidget.refreshIntervalSpinBox.value())

    def open_recording_analysis(self):
        from qspectrumanalyzer.analysis import RecordingAnalysisWindow
        if self.analysis_window is None:
            self.analysis_window = RecordingAnalysisWindow(self)
        self.analysis_window.show()
        self.analysis_window.raise_()
        self.analysis_window.activateWindow()

    def set_trigger_levels(self, power):
        if not np.isfinite(power):
            return
        self.recordingWidget.thresholdSpinBox.setValue(power)
        self.peakListWidget.minPowerSpinBox.setValue(self.recordingWidget.thresholdSpinBox.value())
        self.show_status("Trigger level: {:.1f} dB (Peaks and CSV recording)".format(
            self.recordingWidget.thresholdSpinBox.value()))

    def create_recording_dock(self):
        self.recordingDockWidget = QtWidgets.QDockWidget(self.tr("CSV recording"), self)
        self.recordingDockWidget.setObjectName("recordingDockWidget")
        self.recordingWidget = RecordingWidget(self.recordingDockWidget)
        self.recordingDockWidget.setWidget(self.recordingWidget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea(2), self.recordingDockWidget)
        self.tabifyDockWidget(self.peaksDockWidget, self.recordingDockWidget)
        self.peaksDockWidget.raise_()

    def create_snapshots_dock(self):
        self.displayed_snapshot_paths = {}
        self.snapshot_save_thread = None
        self.snapshot_close_pending = False
        self.snapshotsDockWidget = QtWidgets.QDockWidget(self.tr("Spectrum snapshots"), self)
        self.snapshotsDockWidget.setObjectName("snapshotsDockWidget")
        self.snapshotWidget = SnapshotWidget(self.snapshotsDockWidget)
        self.snapshotWidget.capture_requested.connect(self.capture_snapshot)
        self.snapshotWidget.display_requested.connect(self.display_snapshot)
        self.snapshotWidget.loading_finished.connect(self.snapshot_loading_finished)
        self.snapshotWidget.snapshot_renamed.connect(self.rename_snapshot_display)
        self.snapshotWidget.range_requested.connect(self.apply_snapshot_range)
        self.snapshotsDockWidget.setWidget(self.snapshotWidget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea(2), self.snapshotsDockWidget)
        self.tabifyDockWidget(self.peaksDockWidget, self.snapshotsDockWidget)
        self.peaksDockWidget.raise_()
        self.startFreqSpinBox.valueChanged.connect(self.validate_waterfall_snapshot_range)
        self.stopFreqSpinBox.valueChanged.connect(self.validate_waterfall_snapshot_range)

    def apply_snapshot_range(self, frequencies):
        if frequencies is None:
            return
        start, stop = (float(value) / 1e6 for value in frequencies)
        if not (self.startFreqSpinBox.minimum() <= start <= self.startFreqSpinBox.maximum()
                and self.stopFreqSpinBox.minimum() <= stop <= self.stopFreqSpinBox.maximum()
                and start < stop):
            self.snapshotWidget.error('Snapshot frequency range is not supported by the selected backend')
            return
        answer = QtWidgets.QMessageBox.question(
            self, self.tr('Apply snapshot range'),
            self.tr('Stop scanning if running and set the frequency range to {:.6f}–{:.6f} MHz?').format(start, stop),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.stop()
        self.snapshotWidget.displayCheckBox.setChecked(False)
        self.snapshotWidget.cancel_loading()
        # Apply the two bounds together, without validating an intermediate range.
        self.startFreqSpinBox.blockSignals(True)
        self.stopFreqSpinBox.blockSignals(True)
        self.startFreqSpinBox.setValue(start)
        self.stopFreqSpinBox.setValue(stop)
        self.startFreqSpinBox.blockSignals(False)
        self.stopFreqSpinBox.blockSignals(False)
        self.fit_frequency_range()

    def capture_snapshot(self):
        panel = self.snapshotWidget
        if self.snapshot_save_thread is not None:
            return
        try:
            directory = panel.directoryEdit.text()
            if not directory.strip():
                raise ValueError("Select a snapshot directory")
            if panel.typeComboBox.currentData() == 'waterfall':
                waterfall = self.waterfallPlotWidget.snapshot_data()
                plot = self.waterfallPlotWidget.plot
                rect = self.waterfallPlotLayout.mapFromScene(plot.sceneBoundingRect()).boundingRect()
                image = self.waterfallPlotLayout.grab(rect).toImage()
                curves = []
            else:
                waterfall = None
                curves = self.spectrumPlotWidget.snapshot_data()
                if not curves:
                    raise ValueError("No visible spectrum data to capture")
                plot = self.spectrumPlotWidget.plot
                rect = self.mainPlotLayout.mapFromScene(plot.sceneBoundingRect()).boundingRect()
                image = self.mainPlotLayout.grab(rect).toImage()
            QtCore.QSettings().setValue("snapshots/directory", directory)
            self.snapshot_save_thread = SnapshotSaveThread(
                directory, curves, plot.viewRange(), image, waterfall, parent=self)
            self.snapshot_save_thread.finished.connect(self.snapshot_save_finished)
            panel.captureButton.setEnabled(False)
            panel.statusLabel.setText("Saving snapshot… Measurement can continue.")
            self.snapshot_save_thread.start()
        except (OSError, ValueError) as error:
            panel.error(error)

    @QtCore.Slot()
    def snapshot_loading_finished(self):
        if self.snapshot_close_pending:
            self.close()

    def rename_snapshot_display(self, path, name):
        if self.displayed_snapshot_paths.get('spectrum') == path:
            self.spectrumPlotWidget.rename_snapshot_display(name)
        plot = self.waterfallPlotWidget.snapshot_plot
        if plot is not None and self.displayed_snapshot_paths.get('waterfall') == path:
            plot.setTitle(name or 'Waterfall snapshot')

    @QtCore.Slot()
    def snapshot_save_finished(self):
        worker = self.snapshot_save_thread
        self.snapshot_save_thread = None
        panel = self.snapshotWidget
        panel.captureButton.setEnabled(True)
        if worker.error is not None:
            panel.error(worker.error)
        else:
            if os.path.abspath(os.path.expanduser(panel.directoryEdit.text())) == str(worker.path.parent.resolve()):
                panel.refresh_list(selected=worker.path)
            panel.statusLabel.setText("Snapshot saved (PNG + NPZ)")
            panel.statusLabel.setToolTip(str(worker.path))
        worker.deleteLater()
        if self.snapshot_close_pending:
            self.close()

    def display_snapshot(self, snapshot):
        if snapshot is None:
            self.spectrumPlotWidget.display_snapshot(None)
            self.waterfallPlotWidget.display_snapshot(None)
            self.displayed_snapshot_paths.clear()
            return
        kind = snapshot.get('kind', 'spectrum')
        if kind == 'waterfall':
            if not self.waterfallPlotWidget.enabled:
                return
            if not self.waterfall_ranges_match(snapshot['frequency_range']):
                self.reject_waterfall_snapshot(self.waterfall_range_error(snapshot['frequency_range']))
                return
            self.waterfallPlotWidget.display_snapshot(snapshot)
        else:
            self.spectrumPlotWidget.display_snapshot(snapshot)
        self.displayed_snapshot_paths[kind] = self.snapshotWidget.selected_path()

    def waterfall_ranges_match(self, saved):
        current = sorted((self.startFreqSpinBox.value() * 1e6, self.stopFreqSpinBox.value() * 1e6))
        actual = self.waterfallPlotWidget.image_frequency_range
        return (np.allclose(saved, current, rtol=0, atol=0.01) and
                (actual is None or np.allclose(saved, actual, rtol=0, atol=0.01)))

    def waterfall_range_error(self, saved):
        return ('Waterfall frequency ranges do not match.\n'
                'Saved: {:.6f}–{:.6f} MHz\nCurrent setting: {:.6f}–{:.6f} MHz\n'
                'Use the same range and collect new waterfall data.').format(
                    saved[0] / 1e6, saved[1] / 1e6,
                    self.startFreqSpinBox.value(), self.stopFreqSpinBox.value())

    def reject_waterfall_snapshot(self, message):
        self.waterfallPlotWidget.display_snapshot(None)
        self.displayed_snapshot_paths.pop('waterfall', None)
        if self.spectrumPlotWidget.snapshot_plot is None:
            self.snapshotWidget.displayCheckBox.setChecked(False)
        self.snapshotWidget.error(message)

    def validate_waterfall_snapshot_range(self, *args):
        saved = self.waterfallPlotWidget.snapshot_frequency_range
        if saved is not None and not self.waterfall_ranges_match(saved):
            self.waterfallPlotWidget.display_snapshot(None)
            self.reject_waterfall_snapshot(self.waterfall_range_error(saved))

    def create_view_menu(self):
        """Create actions for closing and reopening dock panels."""
        self.menu_View = QtWidgets.QMenu(self.tr("&View"), self.menubar)
        self.menu_View.setObjectName("menu_View")
        self.menubar.insertMenu(self.menu_Help.menuAction(), self.menu_View)

        self.dock_widgets = (
            self.controlsDockWidget,
            self.frequencyDockWidget,
            self.settingsDockWidget,
            self.levelsDockWidget,
            self.peaksDockWidget,
            self.recordingDockWidget,
            self.snapshotsDockWidget,
        )
        for dock in self.dock_widgets:
            dock.setFeatures(dock.features() | QtWidgets.QDockWidget.DockWidgetClosable)
            action = dock.toggleViewAction()
            action.setText(dock.windowTitle())
            self.menu_View.addAction(action)

        self.menu_View.addSeparator()
        self.actionWaterfall = self.menu_View.addAction(self.tr("Waterfall"))
        self.actionWaterfall.setCheckable(True)
        self.actionWaterfall.setChecked(True)
        self.actionWaterfall.toggled.connect(self.set_waterfall_enabled)
        self.actionShowAllPanels = self.menu_View.addAction(self.tr("Show All Panels"))
        self.actionShowAllPanels.triggered.connect(self.show_all_dock_panels)

    @QtCore.Slot()
    def show_all_dock_panels(self):
        """Show every dock panel after one or more of them were closed."""
        for dock in self.dock_widgets:
            if dock is not self.levelsDockWidget or self.actionWaterfall.isChecked():
                dock.show()

    def update_history_retention(self):
        if self.data_storage is None:
            return
        enabled = self.actionWaterfall.isChecked()
        settings = QtCore.QSettings()
        size = settings.value("waterfall_history_size", 100, int) if enabled else 1
        if self.persistenceCheckBox.isChecked():
            size = max(size, settings.value("persistence_length", 5, int) + 1)
        self.data_storage.configure_history(size, enabled)

    def history_retention_updated(self, storage):
        if storage is not self.data_storage:
            return
        if storage.y is not None:
            # Release any curve reference to a row of the old large history.
            self.spectrumPlotWidget.update_plot(storage, force=True)
        self.spectrumPlotWidget.clear_persistence()
        if self.persistenceCheckBox.isChecked() and storage.history is not None:
            self.spectrumPlotWidget.recalculate_persistence(storage)

    def set_waterfall_enabled(self, enabled):
        self.snapshotWidget.set_waterfall_visible(enabled)
        if not enabled and self.waterfallPlotWidget.snapshot_plot is not None:
            self.displayed_snapshot_paths.pop('waterfall', None)
            if self.spectrumPlotWidget.snapshot_plot is None:
                self.snapshotWidget.displayCheckBox.setChecked(False)
        self.waterfallPlotWidget.set_enabled(enabled)
        self.waterfallPlotLayout.setVisible(enabled)
        self.levelsDockWidget.setVisible(enabled)
        self.levelsDockWidget.toggleViewAction().setEnabled(enabled)
        self.spectrumPlotWidget.plot.setXLink(self.waterfallPlotWidget.plot if enabled else None)
        self.update_history_retention()
        QtCore.QSettings().setValue("waterfall_enabled", int(enabled))
        if enabled:
            self.ensure_plot_splitter_visible()
            if self.data_storage is not None:
                self.waterfallPlotWidget.recalculate_plot(self.data_storage)

    def install_shared_view_all_actions(self):
        """Make every View All path use one range for spectrum and waterfall"""
        for plot in (self.spectrumPlotWidget.plot, self.waterfallPlotWidget.plot):
            view = plot.getViewBox()
            view.autoRange = types.MethodType(lambda view, *args, **kwargs: self.auto_range_plots(), view)
            action = view.menu.viewAll
            try:
                action.triggered.disconnect()
            except (TypeError, RuntimeError):
                pass
            action.triggered.connect(self.auto_range_plots)

    @QtCore.Slot()
    def auto_range_plots(self):
        """Auto-range both plots together, regardless of which menu was used"""
        spectrum_view = self.spectrumPlotWidget.plot.getViewBox()

        def restore_x_link():
            if self.actionWaterfall.isChecked():
                self.spectrumPlotWidget.plot.setXLink(self.waterfallPlotWidget.plot)

        spectrum_view.linkView(spectrum_view.XAxis, None)

        try:
            start_freq = float(self.startFreqSpinBox.value()) * 1e6
            stop_freq = float(self.stopFreqSpinBox.value()) * 1e6
            if start_freq > stop_freq:
                start_freq, stop_freq = stop_freq, start_freq

            if self.data_storage is None or self.data_storage.x is None:
                self.waterfallPlotWidget.view_all(start_freq, stop_freq)
                self.spectrumPlotWidget.plot.setXRange(start_freq, stop_freq, padding=0)
                return

            self.waterfallPlotWidget.view_all(start_freq, stop_freq)
            self.spectrumPlotWidget.plot.setXRange(start_freq, stop_freq, padding=0)

            y_parts = []
            for y in (
                    self.data_storage.y,
                    self.data_storage.average,
                    self.data_storage.peak_hold_max,
                    self.data_storage.peak_hold_min,
                    self.data_storage.baseline):
                if y is None:
                    continue
                y = np.asarray(y)
                y = y[np.isfinite(y)]
                if y.size:
                    y_parts.append(y)

            if y_parts:
                y = np.concatenate(y_parts)
                self.spectrumPlotWidget.plot.setYRange(y.min(), y.max(), padding=0.05)

            self.waterfallPlotWidget.view_all(start_freq, stop_freq)
        finally:
            restore_x_link()

    def setup_power_thread(self):
        """Create power_thread and connect signals to slots"""
        if self.power_thread:
            self.stop()

        settings = QtCore.QSettings()
        self.data_storage = DataStorage(max_history_size=settings.value("waterfall_history_size", 100, int))
        self.data_storage.history_resized.connect(self.history_retention_updated)
        self.update_history_retention()
        self.data_storage.recording_frame_ready.connect(self.recordingWidget.record_frame)
        self.data_storage.data_updated.connect(self.update_data)
        self.data_storage.data_updated.connect(self.spectrumPlotWidget.update_plot)
        self.data_storage.data_updated.connect(self.spectrumPlotWidget.update_persistence)
        self.data_storage.data_recalculated.connect(self.spectrumPlotWidget.recalculate_plot)
        self.data_storage.data_recalculated.connect(self.spectrumPlotWidget.recalculate_persistence)
        self.data_storage.history_updated.connect(self.waterfallPlotWidget.update_plot)
        self.data_storage.history_recalculated.connect(self.waterfallPlotWidget.recalculate_plot)
        self.data_storage.history_updated.connect(self.validate_waterfall_snapshot_range)
        self.data_storage.history_recalculated.connect(self.validate_waterfall_snapshot_range)
        self.data_storage.average_updated.connect(self.spectrumPlotWidget.update_average)
        self.data_storage.average_updated.connect(self.mark_peak_frequencies_dirty)
        self.data_storage.baseline_updated.connect(self.spectrumPlotWidget.update_baseline)
        self.data_storage.peak_hold_max_updated.connect(self.spectrumPlotWidget.update_peak_hold_max)
        self.data_storage.peak_hold_max_updated.connect(self.mark_peak_frequencies_dirty)
        self.data_storage.peak_hold_min_updated.connect(self.spectrumPlotWidget.update_peak_hold_min)

        # Setup default values and limits in case that backend is changed
        backend = settings.value("backend", "soapy_power")
        try:
            backend_module = getattr(backends, backend)
        except AttributeError:
            backend_module = backends.soapy_power

        if self.backend is None or backend != self.backend:
            self.backend = backend
            self.gainSpinBox.setMinimum(backend_module.Info.gain_min)
            self.gainSpinBox.setMaximum(backend_module.Info.gain_max)
            self.gainSpinBox.setValue(backend_module.Info.gain)
            self.startFreqSpinBox.setMinimum(backend_module.Info.start_freq_min)
            self.startFreqSpinBox.setMaximum(backend_module.Info.start_freq_max)
            self.startFreqSpinBox.setValue(backend_module.Info.start_freq)
            self.stopFreqSpinBox.setMinimum(backend_module.Info.stop_freq_min)
            self.stopFreqSpinBox.setMaximum(backend_module.Info.stop_freq_max)
            self.stopFreqSpinBox.setValue(backend_module.Info.stop_freq)
            self.binSizeSpinBox.setMinimum(backend_module.Info.bin_size_min)
            self.binSizeSpinBox.setMaximum(backend_module.Info.bin_size_max)
            self.binSizeSpinBox.setValue(backend_module.Info.bin_size)
            self.intervalSpinBox.setMinimum(backend_module.Info.interval_min)
            self.intervalSpinBox.setMaximum(backend_module.Info.interval_max)
            self.intervalSpinBox.setValue(backend_module.Info.interval)
            self.ppmSpinBox.setMinimum(backend_module.Info.ppm_min)
            self.ppmSpinBox.setMaximum(backend_module.Info.ppm_max)
            self.ppmSpinBox.setValue(backend_module.Info.ppm)
            self.cropSpinBox.setMinimum(backend_module.Info.crop_min)
            self.cropSpinBox.setMaximum(backend_module.Info.crop_max)
            self.cropSpinBox.setValue(backend_module.Info.crop)

        # Setup default values and limits in case that LNB LO is changed
        lnb_lo = settings.value("lnb_lo", 0, float) / 1e6

        start_freq_min = backend_module.Info.start_freq_min + lnb_lo
        start_freq_max = backend_module.Info.start_freq_max + lnb_lo
        start_freq = self.startFreqSpinBox.value()
        stop_freq_min = backend_module.Info.stop_freq_min + lnb_lo
        stop_freq_max = backend_module.Info.stop_freq_max + lnb_lo
        stop_freq = self.stopFreqSpinBox.value()

        self.startFreqSpinBox.setMinimum(start_freq_min if start_freq_min > 0 else 0)
        self.startFreqSpinBox.setMaximum(start_freq_max)
        if start_freq < start_freq_min or start_freq > start_freq_max:
            self.startFreqSpinBox.setValue(start_freq_min)

        self.stopFreqSpinBox.setMinimum(stop_freq_min if stop_freq_min > 0 else 0)
        self.stopFreqSpinBox.setMaximum(stop_freq_max)
        if stop_freq < stop_freq_min or stop_freq > stop_freq_max:
            self.stopFreqSpinBox.setValue(stop_freq_max)

        self.power_thread = backend_module.PowerThread(self.data_storage)
        self.power_thread.powerThreadStarted.connect(self.on_power_thread_started)
        self.power_thread.powerThreadStopped.connect(self.on_power_thread_stopped)

    def set_dock_size(self, dock, width, height):
        """Ugly hack for resizing QDockWidget (because it doesn't respect minimumSize / sizePolicy set in Designer)
           Link: https://stackoverflow.com/questions/2722939/c-resize-a-docked-qt-qdockwidget-programmatically"""
        old_min_size = dock.minimumSize()
        old_max_size = dock.maximumSize()

        if width >= 0:
            if dock.width() < width:
                dock.setMinimumWidth(width)
            else:
                dock.setMaximumWidth(width)

        if height >= 0:
            if dock.height() < height:
                dock.setMinimumHeight(height)
            else:
                dock.setMaximumHeight(height)

        QtCore.QTimer.singleShot(0, lambda: self.set_dock_size_callback(dock, old_min_size, old_max_size))

    def set_dock_size_callback(self, dock, old_min_size, old_max_size):
        """Return to original QDockWidget minimumSize and maximumSize after running set_dock_size()"""
        dock.setMinimumSize(old_min_size)
        dock.setMaximumSize(old_max_size)

    def load_settings(self):
        """Restore spectrum analyzer settings and window geometry"""
        settings = QtCore.QSettings()
        self.startFreqSpinBox.setValue(settings.value("start_freq", 87.0, float))
        self.stopFreqSpinBox.setValue(settings.value("stop_freq", 108.0, float))
        self.binSizeSpinBox.setValue(settings.value("bin_size", 10.0, float))
        self.intervalSpinBox.setValue(settings.value("interval", 10.0, float))
        self.gainSpinBox.setValue(settings.value("gain", 0, float))
        self.ppmSpinBox.setValue(settings.value("ppm", 0, int))
        self.cropSpinBox.setValue(settings.value("crop", 0, int))
        self.mainCurveCheckBox.setChecked(settings.value("main_curve", 1, int))
        self.peakHoldMaxCheckBox.setChecked(settings.value("peak_hold_max", 0, int))
        self.peakHoldMinCheckBox.setChecked(settings.value("peak_hold_min", 0, int))
        self.averageCheckBox.setChecked(settings.value("average", 0, int))
        self.smoothCheckBox.setChecked(settings.value("smooth", 0, int))
        self.persistenceCheckBox.setChecked(settings.value("persistence", 0, int))
        self.baselineCheckBox.setChecked(settings.value("baseline", 0, int))
        self.subtractBaselineCheckBox.setChecked(settings.value("subtract_baseline", 0, int))

        # Restore window state
        if settings.value("window_state"):
            state = settings.value("window_state")
            self.restoreState(state)
            # Layouts saved before snapshots existed do not position the new
            # dock. Leaving it below Settings makes the window taller than the
            # screen. Migrate only old layouts, preserving newer arrangements.
            if "snapshotsDockWidget".encode("utf-16-be") not in bytes(state):
                self.tabifyDockWidget(self.peaksDockWidget, self.snapshotsDockWidget)
                self.peaksDockWidget.raise_()
        if settings.value("plotsplitter_state"):
            self.plotSplitter.restoreState(settings.value("plotsplitter_state"))
        self.actionWaterfall.setChecked(bool(settings.value("waterfall_enabled", 1, int)))

        # Migration from older version of config file
        if settings.value("config_version", 1, int) < 2:
            # Make tabs from docks when started for first time
            self.tabifyDockWidget(self.settingsDockWidget, self.levelsDockWidget)
            self.settingsDockWidget.raise_()
            self.set_dock_size(self.controlsDockWidget, 0, 0)
            self.set_dock_size(self.frequencyDockWidget, 0, 0)
            # Update config version
            settings.setValue("config_version", 2)

        # Window geometry has to be restored only after show(), because initial
        # maximization doesn't work otherwise (at least not in some window managers on X11)
        self.show()
        QtCore.QTimer.singleShot(0, self.ensure_plot_splitter_visible)
        if settings.value("window_geometry"):
            self.restoreGeometry(settings.value("window_geometry"))
            QtCore.QTimer.singleShot(0, self.ensure_plot_splitter_visible)
        QtCore.QTimer.singleShot(0, self.fit_frequency_range)
        QtCore.QTimer.singleShot(0, self.ensure_window_visible)

    def ensure_window_visible(self):
        """Restore an accessible window even after screen/layout changes."""
        if self.isMinimized():
            self.showNormal()
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry()
        if not self.isMaximized() and not self.isFullScreen():
            frame = self.frameGeometry()
            if not available.contains(frame):
                extra_width = frame.width() - self.width()
                extra_height = frame.height() - self.height()
                self.resize(min(self.width(), available.width() - extra_width),
                            min(self.height(), available.height() - extra_height))
                self.move(available.topLeft())
        self.raise_()
        self.activateWindow()

    def fit_frequency_range(self):
        """Show configured frequencies without depending on waterfall linkage."""
        start, stop = sorted((self.startFreqSpinBox.value() * 1e6,
                              self.stopFreqSpinBox.value() * 1e6))
        if self.actionWaterfall.isChecked():
            self.waterfallPlotWidget.set_frequency_range(start, stop)
        self.spectrumPlotWidget.plot.setXRange(start, stop, padding=0)

    def ensure_plot_splitter_visible(self):
        """Prevent restored splitter state from hiding one of the plots"""
        if not self.actionWaterfall.isChecked():
            return
        sizes = self.plotSplitter.sizes()
        if not sizes or all(size > 0 for size in sizes):
            return

        height = self.plotSplitter.height()
        if height <= 0:
            height = sum(sizes)
        if height <= 0:
            height = 2

        first_size = height // 2
        self.plotSplitter.setSizes([first_size, height - first_size])

    def save_settings(self):
        """Save spectrum analyzer settings and window geometry"""
        settings = QtCore.QSettings()
        settings.setValue("start_freq", self.startFreqSpinBox.value())
        settings.setValue("stop_freq", self.stopFreqSpinBox.value())
        settings.setValue("bin_size", self.binSizeSpinBox.value())
        settings.setValue("interval", self.intervalSpinBox.value())
        settings.setValue("gain", self.gainSpinBox.value())
        settings.setValue("ppm", self.ppmSpinBox.value())
        settings.setValue("crop", self.cropSpinBox.value())
        settings.setValue("main_curve", int(self.mainCurveCheckBox.isChecked()))
        settings.setValue("peak_hold_max", int(self.peakHoldMaxCheckBox.isChecked()))
        settings.setValue("peak_hold_min", int(self.peakHoldMinCheckBox.isChecked()))
        settings.setValue("average", int(self.averageCheckBox.isChecked()))
        settings.setValue("smooth", int(self.smoothCheckBox.isChecked()))
        settings.setValue("persistence", int(self.persistenceCheckBox.isChecked()))
        settings.setValue("baseline", int(self.baselineCheckBox.isChecked()))
        settings.setValue("subtract_baseline", int(self.subtractBaselineCheckBox.isChecked()))

        # Save window state and geometry
        settings.setValue("window_geometry", self.saveGeometry())
        settings.setValue("window_state", self.saveState())
        settings.setValue("plotsplitter_state", self.plotSplitter.saveState())

    def show_status(self, message, timeout=2000):
        """Show message in status bar"""
        self.statusbar.showMessage(message, timeout)

    def update_buttons(self):
        """Update state of control buttons"""
        self.startButton.setEnabled(not self.power_thread.alive)
        self.singleShotButton.setEnabled(not self.power_thread.alive)
        self.stopButton.setEnabled(self.power_thread.alive)

    def update_data(self, data_storage):
        """Update GUI when new data is received"""
        timestamp = time.time()
        self.prev_sweep_time = timestamp - self.prev_data_timestamp
        self.prev_data_timestamp = timestamp
        self.update_status()

    def update_status(self):
        """Update status bar"""
        timestamp = time.time()
        status = []

        if self.power_thread.params["hops"]:
            status.append(self.tr("Frequency hops: {}").format(self.power_thread.params["hops"]))

        status.append(self.tr("Total time: {} | Sweep time: {:.2f} s ({:.2f} FPS)").format(
            human_time(timestamp - self.start_timestamp),
            self.prev_sweep_time,
            (1 / self.prev_sweep_time) if self.prev_sweep_time else 0
        ))

        self.show_status(" | ".join(status), timeout=0)
        self.update_progress(timestamp - self.prev_data_timestamp)

    def update_progress(self, value):
        """Update progress bar"""
        value = int(round(value * 1000))
        value_max = int(round(self.intervalSpinBox.value() * 1000))

        if value_max < 1000:
            return

        if value > value_max + 1000:
            self.progressbar.setRange(0, 0)
            value = value_max
        elif value > value_max:
            value = value_max
        else:
            self.progressbar.setRange(0, value_max)

        self.progressbar.setValue(value)

    def on_power_thread_started(self):
        """Update buttons state when power thread is started"""
        self.update_buttons()
        self.progressbar.setVisible(True)

    def on_power_thread_stopped(self):
        """Update buttons state and status bar when power thread is stopped"""
        self.update_buttons()
        self.update_status_timer.stop()
        self.update_status()
        self.progressbar.setVisible(False)

    def start(self, single_shot=False):
        """Start power thread"""
        settings = QtCore.QSettings()
        executable = settings.value("executable", self.backend or "soapy_power")
        try:
            command = shlex.split(executable)
            if not command or shutil.which(command[0]) is None:
                raise ValueError("Backend executable not found: {}\n"
                                 "Open File > Settings and select an installed backend "
                                 "and its executable path.".format(executable))
        except ValueError as error:
            QtWidgets.QMessageBox.warning(self, "Cannot start measurement", str(error))
            return

        self.prev_sweep_time = 0
        self.prev_data_timestamp = time.time()
        self.start_timestamp = self.prev_data_timestamp

        if self.intervalSpinBox.value() >= 1:
            self.progressbar.setRange(0, int(round(self.intervalSpinBox.value() * 1000)))
        else:
            self.progressbar.setRange(0, 0)
        self.update_progress(0)
        self.update_status_timer.start(100)

        self.waterfallPlotWidget.history_size = settings.value("waterfall_history_size", 100, int)
        self.waterfallPlotWidget.clear_plot()

        self.spectrumPlotWidget.main_curve = bool(self.mainCurveCheckBox.isChecked())
        self.spectrumPlotWidget.main_color = str_to_color(settings.value("main_color", "255, 255, 0, 255"))
        self.spectrumPlotWidget.peak_hold_max = bool(self.peakHoldMaxCheckBox.isChecked())
        self.spectrumPlotWidget.peak_hold_max_color = str_to_color(settings.value("peak_hold_max_color", "255, 0, 0, 255"))
        self.spectrumPlotWidget.peak_hold_min = bool(self.peakHoldMinCheckBox.isChecked())
        self.spectrumPlotWidget.peak_hold_min_color = str_to_color(settings.value("peak_hold_min_color", "0, 0, 255, 255"))
        self.spectrumPlotWidget.average = bool(self.averageCheckBox.isChecked())
        self.spectrumPlotWidget.average_color = str_to_color(settings.value("average_color", "0, 255, 255, 255"))
        self.spectrumPlotWidget.baseline = bool(self.baselineCheckBox.isChecked())
        self.spectrumPlotWidget.baseline_color = str_to_color(settings.value("baseline_color", "255, 0, 255, 255"))
        self.spectrumPlotWidget.persistence = bool(self.persistenceCheckBox.isChecked())
        self.spectrumPlotWidget.persistence_length = settings.value("persistence_length", 5, int)
        self.spectrumPlotWidget.persistence_decay = settings.value("persistence_decay", "exponential")
        self.spectrumPlotWidget.persistence_color = str_to_color(settings.value("persistence_color", "0, 255, 0, 255"))
        self.spectrumPlotWidget.clear_plot()
        self.spectrumPlotWidget.clear_peak_hold_max()
        self.spectrumPlotWidget.clear_peak_hold_min()
        self.spectrumPlotWidget.clear_average()
        self.spectrumPlotWidget.clear_baseline()
        self.spectrumPlotWidget.clear_persistence()
        self.peakListWidget.clear()
        self.peak_update_dirty = False

        self.data_storage.reset()
        start_freq = float(self.startFreqSpinBox.value()) * 1e6
        stop_freq = float(self.stopFreqSpinBox.value()) * 1e6
        self.data_storage.set_frequency_range(start_freq, stop_freq)
        self.fit_frequency_range()
        self.data_storage.set_smooth(
            bool(self.smoothCheckBox.isChecked()),
            settings.value("smooth_length", 11, int),
            settings.value("smooth_window", "hanning")
        )
        self.data_storage.set_subtract_baseline(
            bool(self.subtractBaselineCheckBox.isChecked()),
            settings.value("baseline_file", None)
        )

        if not self.power_thread.alive:
            self.power_thread.setup(
                float(self.startFreqSpinBox.value()),
                float(self.stopFreqSpinBox.value()),
                float(self.binSizeSpinBox.value()),
                interval=float(self.intervalSpinBox.value()),
                gain=float(self.gainSpinBox.value()),
                ppm=int(self.ppmSpinBox.value()),
                crop=int(self.cropSpinBox.value()) / 100.0,
                single_shot=single_shot,
                device=settings.value("device", ""),
                sample_rate=settings.value("sample_rate", 2560000, float),
                bandwidth=settings.value("bandwidth", 0, float),
                lnb_lo=settings.value("lnb_lo", 0, float)
            )
            self.power_thread.start()

    def stop(self):
        """Stop power thread"""
        if self.power_thread.alive:
            self.power_thread.stop()

    @QtCore.Slot()
    def on_startButton_clicked(self):
        self.start()

    @QtCore.Slot()
    def on_singleShotButton_clicked(self):
        self.start(single_shot=True)

    @QtCore.Slot()
    def on_stopButton_clicked(self):
        self.stop()

    @QtCore.Slot(bool)
    def on_mainCurveCheckBox_toggled(self, checked):
        self.spectrumPlotWidget.main_curve = checked
        if self.spectrumPlotWidget.curve.xData is None:
            self.spectrumPlotWidget.update_plot(self.data_storage)
        self.spectrumPlotWidget.curve.setVisible(checked)

    @QtCore.Slot(bool)
    def on_peakHoldMaxCheckBox_toggled(self, checked):
        self.spectrumPlotWidget.peak_hold_max = checked
        if self.spectrumPlotWidget.curve_peak_hold_max.xData is None:
            self.spectrumPlotWidget.update_peak_hold_max(self.data_storage)
        self.spectrumPlotWidget.curve_peak_hold_max.setVisible(checked)
        if checked:
            self.mark_peak_frequencies_dirty(self.data_storage)
            if not self.peak_update_timer.isActive():
                self.refresh_peak_frequencies()

    @QtCore.Slot(bool)
    def on_peakHoldMinCheckBox_toggled(self, checked):
        self.spectrumPlotWidget.peak_hold_min = checked
        if self.spectrumPlotWidget.curve_peak_hold_min.xData is None:
            self.spectrumPlotWidget.update_peak_hold_min(self.data_storage)
        self.spectrumPlotWidget.curve_peak_hold_min.setVisible(checked)

    @QtCore.Slot(bool)
    def on_averageCheckBox_toggled(self, checked):
        self.spectrumPlotWidget.average = checked
        if self.spectrumPlotWidget.curve_average.xData is None:
            self.spectrumPlotWidget.update_average(self.data_storage)
        self.spectrumPlotWidget.curve_average.setVisible(checked)
        if checked:
            self.mark_peak_frequencies_dirty(self.data_storage)
            if not self.peak_update_timer.isActive():
                self.refresh_peak_frequencies()
        else:
            self.peak_update_dirty = False
            self.peakListWidget.clear()

    @QtCore.Slot(object)
    def mark_peak_frequencies_dirty(self, data_storage):
        """Remember that average peak data changed without updating the table."""
        self.peak_update_dirty = True

    @QtCore.Slot()
    def refresh_peak_frequencies_if_dirty(self):
        """Refresh peak list from timer only when average data changed."""
        if self.peak_update_dirty:
            self.refresh_peak_frequencies()

    @QtCore.Slot()
    def refresh_peak_frequencies(self):
        """Update peak list from average data when requested."""
        if self.data_storage is None:
            return

        source = self.peakListWidget.sourceComboBox.currentData()
        if source == "peak_hold_max":
            y = self.data_storage.peak_hold_max
        else:
            y = self.data_storage.average

        self.peakListWidget.update_peaks(self.data_storage.x, y)
        self.peak_update_dirty = False

    @QtCore.Slot(str)
    def set_peak_source(self, source):
        """Switch peak list data source."""
        self.mark_peak_frequencies_dirty(self.data_storage)
        if not self.peak_update_timer.isActive():
            self.refresh_peak_frequencies()

    @QtCore.Slot(float)
    def set_peak_min_power(self, min_power):
        """Apply peak minimum power filter."""
        self.mark_peak_frequencies_dirty(self.data_storage)
        if not self.peak_update_timer.isActive():
            self.refresh_peak_frequencies()

    @QtCore.Slot(float)
    def set_peak_band_floor(self, band_floor):
        """Apply signal-band merge floor."""
        self.mark_peak_frequencies_dirty(self.data_storage)
        if not self.peak_update_timer.isActive():
            self.refresh_peak_frequencies()

    @QtCore.Slot(bool)
    def set_peak_bands_enabled(self, enabled):
        """Toggle grouped signal-band peak display."""
        self.mark_peak_frequencies_dirty(self.data_storage)
        if not self.peak_update_timer.isActive():
            self.refresh_peak_frequencies()

    @QtCore.Slot(bool)
    def set_peak_auto_refresh(self, enabled):
        """Toggle periodic peak list updates."""
        if enabled:
            self.peak_update_timer.start()
            self.refresh_peak_frequencies_if_dirty()
        else:
            self.peak_update_timer.stop()

    @QtCore.Slot(float)
    def set_peak_refresh_interval(self, seconds):
        """Set peak list refresh interval in seconds."""
        self.peak_update_timer.setInterval(max(100, int(seconds * 1000)))
        if self.peak_update_timer.isActive():
            self.peak_update_timer.start()

    @QtCore.Slot(bool)
    def on_persistenceCheckBox_toggled(self, checked):
        self.spectrumPlotWidget.persistence = checked
        self.update_history_retention()
        if self.spectrumPlotWidget.persistence_curves[0].xData is None:
            self.spectrumPlotWidget.recalculate_persistence(self.data_storage)
        for curve in self.spectrumPlotWidget.persistence_curves:
            curve.setVisible(checked)

    @QtCore.Slot(bool)
    def on_smoothCheckBox_toggled(self, checked):
        settings = QtCore.QSettings()
        self.data_storage.set_smooth(
            checked,
            settings.value("smooth_length", 11, int),
            settings.value("smooth_window", "hanning")
        )

    @QtCore.Slot(bool)
    def on_baselineCheckBox_toggled(self, checked):
        self.spectrumPlotWidget.baseline = checked
        if self.spectrumPlotWidget.curve_baseline.xData is None:
            self.spectrumPlotWidget.update_baseline(self.data_storage)
        self.spectrumPlotWidget.curve_baseline.setVisible(checked)

    @QtCore.Slot(bool)
    def on_subtractBaselineCheckBox_toggled(self, checked):
        settings = QtCore.QSettings()
        self.data_storage.set_subtract_baseline(
            checked,
            settings.value("baseline_file", None)
        )

    @QtCore.Slot()
    def on_baselineButton_clicked(self):
        dialog = QSpectrumAnalyzerBaseline(self)
        if dialog.exec_():
            settings = QtCore.QSettings()
            self.data_storage.set_subtract_baseline(
                bool(self.subtractBaselineCheckBox.isChecked()),
                settings.value("baseline_file", None)
            )

    @QtCore.Slot()
    def on_smoothButton_clicked(self):
        dialog = QSpectrumAnalyzerSmoothing(self)
        if dialog.exec_():
            settings = QtCore.QSettings()
            self.data_storage.set_smooth(
                bool(self.smoothCheckBox.isChecked()),
                settings.value("smooth_length", 11, int),
                settings.value("smooth_window", "hanning")
            )

    @QtCore.Slot()
    def on_persistenceButton_clicked(self):
        prev_persistence_length = self.spectrumPlotWidget.persistence_length
        dialog = QSpectrumAnalyzerPersistence(self)
        if dialog.exec_():
            settings = QtCore.QSettings()
            persistence_length = settings.value("persistence_length", 5, int)
            self.spectrumPlotWidget.persistence_length = persistence_length
            self.spectrumPlotWidget.persistence_decay = settings.value("persistence_decay", "exponential")
            self.update_history_retention()

            # If only decay function has been changed, just reset colors
            if persistence_length == prev_persistence_length:
                self.spectrumPlotWidget.set_colors()
            else:
                self.spectrumPlotWidget.recalculate_persistence(self.data_storage)

    @QtCore.Slot()
    def on_colorsButton_clicked(self):
        dialog = QSpectrumAnalyzerColors(self)
        if dialog.exec_():
            settings = QtCore.QSettings()
            self.spectrumPlotWidget.main_color = str_to_color(settings.value("main_color", "255, 255, 0, 255"))
            self.spectrumPlotWidget.peak_hold_max_color = str_to_color(settings.value("peak_hold_max_color", "255, 0, 0, 255"))
            self.spectrumPlotWidget.peak_hold_min_color = str_to_color(settings.value("peak_hold_min_color", "0, 0, 255, 255"))
            self.spectrumPlotWidget.average_color = str_to_color(settings.value("average_color", "0, 255, 255, 255"))
            self.spectrumPlotWidget.persistence_color = str_to_color(settings.value("persistence_color", "0, 255, 0, 255"))
            self.spectrumPlotWidget.baseline_color = str_to_color(settings.value("baseline_color", "255, 0, 255, 255"))
            self.spectrumPlotWidget.set_colors()

    @QtCore.Slot()
    def on_action_Settings_triggered(self):
        dialog = QSpectrumAnalyzerSettings(self)
        if dialog.exec_():
            self.setup_power_thread()

    @QtCore.Slot()
    def on_action_About_triggered(self):
        QtWidgets.QMessageBox.information(self, self.tr("About - QSpectrumAnalyzer"),
                                          self.tr("QSpectrumAnalyzer {}").format(__version__))

    @QtCore.Slot()
    def on_action_Quit_triggered(self):
        self.close()

    def closeEvent(self, event):
        """Save settings when main window is closed"""
        if (self.snapshot_save_thread is not None or self.snapshotWidget.load_thread is not None
                or self.snapshotWidget.rename_thread is not None):
            self.snapshot_close_pending = True
            self.snapshotWidget.cancel_loading()
            self.stop()
            self.snapshotWidget.statusLabel.setText("Finishing snapshot operation before closing…")
            event.ignore()
            return
        self.stop()
        if self.analysis_window is not None:
            self.analysis_window.close()
        self.recordingWidget.stop_recording()
        self.recordingWidget.save_settings()
        self.save_settings()


def main():
    global debug

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        prog="qspectrumanalyzer",
        description="Spectrum analyzer for multiple SDR platforms",
    )
    parser.add_argument("--debug", action="store_true",
                        help="detailed debugging messages")
    parser.add_argument("--version", action="version",
                        version="%(prog)s {}".format(__version__))
    args, unparsed_args = parser.parse_known_args()
    debug = args.debug

    try:
        # Hide console window on Windows
        if sys.platform == 'win32' and not debug:
            from qspectrumanalyzer import windows
            windows.set_attached_console_visible(False)

        # Start PyQt application
        app = QtWidgets.QApplication(sys.argv[:1] + unparsed_args)
        app.setOrganizationName("QSpectrumAnalyzer")
        app.setOrganizationDomain("qspectrumanalyzer.eutopia.cz")
        app.setApplicationName("QSpectrumAnalyzer")
        window = QSpectrumAnalyzerMainWindow()
        sys.exit(app.exec_())
    finally:
        # Unhide console window on Windows (we don't want to leave zombies behind)
        if sys.platform == 'win32' and not debug:
            windows.set_attached_console_visible(True)


if __name__ == "__main__":
    main()
