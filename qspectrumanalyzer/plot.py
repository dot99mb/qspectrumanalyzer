import collections, math

from Qt import QtCore, QtGui
import numpy as np
import pyqtgraph as pg

# Basic PyQtGraph settings
pg.setConfigOptions(antialias=True)


def set_curve_data(curve, x, y, name):
    """Set curve data, trimming mismatched axes instead of dropping the plot"""
    if x is None or y is None:
        return

    x = np.asarray(x)
    y = np.asarray(y)
    if len(x) != len(y):
        size = min(len(x), len(y))
        print("{} plot data length mismatch: x={}, y={}; trimming to {}".format(
            name, len(x), len(y), size
        ))
        x = x[:size]
        y = y[:size]

    curve.setData(x, y)


class SpectrumPlotWidget:
    """Main spectrum plot"""
    def __init__(self, layout):
        self.peak_cursor_callback = None
        self.trigger_level_callback = None
        self.snapshot_curves = []
        self.snapshot_legend = None
        if not isinstance(layout, pg.GraphicsLayoutWidget):
            raise ValueError("layout must be instance of pyqtgraph.GraphicsLayoutWidget")

        self.layout = layout

        self.main_curve = True
        self.main_color = pg.mkColor("y")
        self.persistence = False
        self.persistence_length = 5
        self.persistence_decay = "exponential"
        self.persistence_color = pg.mkColor("g")
        self.persistence_data = None
        self.persistence_curves = None
        self.peak_hold_max = False
        self.peak_hold_max_color = pg.mkColor("r")
        self.peak_hold_min = False
        self.peak_hold_min_color = pg.mkColor("b")
        self.average = False
        self.average_color = pg.mkColor("c")
        self.baseline = False
        self.baseline_color = pg.mkColor("m")

        self.create_plot()

    def create_plot(self):
        """Create main spectrum plot"""
        self.posLabel = self.layout.addLabel(row=0, col=0, justify="right")
        self.plot = self.layout.addPlot(row=1, col=0)
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel("left", "Power", units="dB")
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLimits(xMin=0)
        self.plot.showButtons()

        #self.plot.setDownsampling(mode="peak")
        #self.plot.setClipToView(True)

        self.create_baseline_curve()
        self.create_persistence_curves()
        self.create_average_curve()
        self.create_peak_hold_min_curve()
        self.create_peak_hold_max_curve()
        self.create_main_curve()

        # Create crosshair
        self.vLine = pg.InfiniteLine(angle=90, movable=False)
        self.vLine.setZValue(1000)
        self.hLine = pg.InfiniteLine(angle=0, movable=False)
        self.vLine.setZValue(1000)
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.hLine, ignoreBounds=True)
        self.mouseProxy = pg.SignalProxy(self.plot.scene().sigMouseMoved,
                                         rateLimit=60, slot=self.mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self.mouse_clicked)

    def snapshot_data(self):
        """Copy exactly the visible plotted curves, including their own axes."""
        candidates = [('Main', self.curve), ('Average', self.curve_average),
                      ('Max hold', self.curve_peak_hold_max), ('Min hold', self.curve_peak_hold_min),
                      ('Baseline', self.curve_baseline)]
        candidates.extend(('Persistence {}'.format(i + 1), curve) for i, curve in enumerate(self.persistence_curves))
        candidates.extend(('Snapshot {}'.format(i + 1), curve) for i, curve in enumerate(self.snapshot_curves))
        result = []
        for name, curve in candidates:
            x, y = curve.getOriginalDataset()
            if not curve.isVisible() or x is None or y is None or not len(x):
                continue
            result.append(dict(name=name, x=np.array(x, copy=True), y=np.array(y, copy=True),
                               color=list(pg.mkPen(curve.opts['pen']).color().getRgb())))
        return result

    def display_snapshot(self, snapshot):
        for curve in self.snapshot_curves:
            self.plot.removeItem(curve)
        self.snapshot_curves = []
        if self.snapshot_legend is not None:
            self.snapshot_legend.clear()
        if snapshot is None:
            return
        if self.snapshot_legend is None:
            self.snapshot_legend = self.plot.addLegend(offset=(10, 10))
        for record in snapshot['curves']:
            # Thick antialiased dashed paths can block Qt's raster painter for
            # seconds on full sweeps. Reduce only display data, preserving peaks
            # and the original arrays for zooming and subsequent snapshots.
            pen = pg.mkPen(tuple(record['color']), width=1)
            curve = self.plot.plot(record['x'], record['y'], pen=pen,
                                   antialias=False,
                                   name=(snapshot.get('name') or 'Snapshot') + ': ' + record['name'])
            curve.setDownsampling(auto=True, method='peak')
            curve.setClipToView(True)
            curve.setZValue(950)
            self.snapshot_curves.append(curve)

    def create_main_curve(self):
        """Create main spectrum curve"""
        self.curve = self.plot.plot(pen=self.main_color)
        self.curve.setZValue(900)

    def create_peak_hold_max_curve(self):
        """Create max. peak hold curve"""
        self.curve_peak_hold_max = self.plot.plot(pen=self.peak_hold_max_color)
        self.curve_peak_hold_max.setZValue(800)

    def create_peak_hold_min_curve(self):
        """Create min. peak hold curve"""
        self.curve_peak_hold_min = self.plot.plot(pen=self.peak_hold_min_color)
        self.curve_peak_hold_min.setZValue(800)

    def create_average_curve(self):
        """Create average curve"""
        self.curve_average = self.plot.plot(pen=self.average_color)
        self.curve_average.setZValue(700)

    def create_baseline_curve(self):
        """Create baseline curve"""
        self.curve_baseline = self.plot.plot(pen=self.baseline_color)
        self.curve_baseline.setZValue(500)

    def create_persistence_curves(self):
        """Create spectrum persistence curves"""
        z_index_base = 600
        decay = self.get_decay()
        self.persistence_curves = []
        for i in range(self.persistence_length):
            alpha = 255 * decay(i + 1, self.persistence_length + 1)
            color = self.persistence_color
            curve = self.plot.plot(pen=(color.red(), color.green(), color.blue(), alpha))
            curve.setZValue(z_index_base - i)
            self.persistence_curves.append(curve)

    def set_colors(self):
        """Set colors of all curves"""
        self.curve.setPen(self.main_color)
        self.curve_peak_hold_max.setPen(self.peak_hold_max_color)
        self.curve_peak_hold_min.setPen(self.peak_hold_min_color)
        self.curve_average.setPen(self.average_color)
        self.curve_baseline.setPen(self.baseline_color)

        decay = self.get_decay()
        for i, curve in enumerate(self.persistence_curves):
            alpha = 255 * decay(i + 1, self.persistence_length + 1)
            color = self.persistence_color
            curve.setPen((color.red(), color.green(), color.blue(), alpha))

    def decay_linear(self, x, length):
        """Get alpha value for persistence curve (linear decay)"""
        return (-x / length) + 1

    def decay_exponential(self, x, length, const=1 / 3):
        """Get alpha value for persistence curve (exponential decay)"""
        return math.e**(-x / (length * const))

    def get_decay(self):
        """Get decay function"""
        if self.persistence_decay == 'exponential':
            return self.decay_exponential
        else:
            return self.decay_linear

    def update_plot(self, data_storage, force=False):
        """Update main spectrum curve"""
        if data_storage.x is None:
            return

        if self.main_curve or force:
            set_curve_data(self.curve, data_storage.x, data_storage.y, "Main spectrum")
            if force:
                self.curve.setVisible(self.main_curve)

    def update_peak_hold_max(self, data_storage, force=False):
        """Update max. peak hold curve"""
        if data_storage.x is None:
            return

        if self.peak_hold_max or force:
            set_curve_data(self.curve_peak_hold_max, data_storage.x,
                           data_storage.peak_hold_max, "Max. hold")
            if force:
                self.curve_peak_hold_max.setVisible(self.peak_hold_max)

    def update_peak_hold_min(self, data_storage, force=False):
        """Update min. peak hold curve"""
        if data_storage.x is None:
            return

        if self.peak_hold_min or force:
            set_curve_data(self.curve_peak_hold_min, data_storage.x,
                           data_storage.peak_hold_min, "Min. hold")
            if force:
                self.curve_peak_hold_min.setVisible(self.peak_hold_min)

    def update_average(self, data_storage, force=False):
        """Update average curve"""
        if data_storage.x is None:
            return

        if self.average or force:
            set_curve_data(self.curve_average, data_storage.x, data_storage.average, "Average")
            if force:
                self.curve_average.setVisible(self.average)

    def update_baseline(self, data_storage, force=False):
        """Update baseline curve"""
        if data_storage.baseline_x is None or data_storage.baseline is None:
            self.curve_baseline.clear()
            return

        if self.baseline or force:
            set_curve_data(self.curve_baseline, data_storage.baseline_x,
                           data_storage.baseline, "Baseline")
            if force:
                self.curve_baseline.setVisible(self.baseline)

    def update_persistence(self, data_storage, force=False):
        """Update persistence curves"""
        if data_storage.x is None:
            return

        if self.persistence or force:
            if self.persistence_data is None:
                self.persistence_data = collections.deque(maxlen=self.persistence_length)
            else:
                for i, y in enumerate(self.persistence_data):
                    curve = self.persistence_curves[i]
                    set_curve_data(curve, data_storage.x, y, "Persistence")
                    if force:
                        curve.setVisible(self.persistence)
            self.persistence_data.appendleft(data_storage.y)

    def recalculate_plot(self, data_storage):
        """Recalculate plot from history"""
        if data_storage.x is None:
            return

        QtCore.QTimer.singleShot(0, lambda: self.update_plot(data_storage, force=True))
        QtCore.QTimer.singleShot(0, lambda: self.update_average(data_storage, force=True))
        QtCore.QTimer.singleShot(0, lambda: self.update_baseline(data_storage, force=True))
        QtCore.QTimer.singleShot(0, lambda: self.update_peak_hold_max(data_storage, force=True))
        QtCore.QTimer.singleShot(0, lambda: self.update_peak_hold_min(data_storage, force=True))

    def recalculate_persistence(self, data_storage):
        """Recalculate persistence data and update persistence curves"""
        if data_storage.x is None:
            return

        self.clear_persistence()
        self.persistence_data = collections.deque(maxlen=self.persistence_length)
        for i in range(min(self.persistence_length, data_storage.history.history_size - 1)):
            data = data_storage.history[-i - 2]
            if data_storage.smooth:
                data = data_storage.smooth_data(data)
            self.persistence_data.append(data)
        QtCore.QTimer.singleShot(0, lambda: self.update_persistence(data_storage, force=True))

    def mouse_moved(self, evt):
        """Update crosshair when mouse is moved"""
        pos = evt[0]
        if self.plot.sceneBoundingRect().contains(pos):
            mousePoint = self.plot.vb.mapSceneToView(pos)
            self.set_cursor(mousePoint.x(), mousePoint.y())

    def set_cursor(self, frequency, power):
        self.posLabel.setText(
            "<span style='font-size: 12pt'>f={:.6f} MHz, P={:.3f} dB</span>".format(frequency / 1e6, power))
        self.vLine.setPos(frequency)
        self.hLine.setPos(power)

    def mouse_clicked(self, event):
        if (event.button() == QtCore.Qt.LeftButton and
                self.plot.vb.sceneBoundingRect().contains(event.scenePos())):
            point = self.plot.vb.mapSceneToView(event.scenePos())
            if event.double() and self.trigger_level_callback:
                self.trigger_level_callback(point.y())
                event.accept()
                return
            if not self.peak_cursor_callback:
                return
            pixel_x, pixel_y = self.plot.vb.viewPixelSize()
            # Hit-test both coordinates within 8 screen pixels, independent of
            # zoom. Clicking empty space must not jump to a neighboring peak.
            self.peak_cursor_callback(point.x(), point.y(), abs(pixel_x) * 8, abs(pixel_y) * 8)

    def clear_plot(self):
        """Clear main spectrum curve"""
        self.curve.clear()

    def clear_peak_hold_max(self):
        """Clear max. peak hold curve"""
        self.curve_peak_hold_max.clear()

    def clear_peak_hold_min(self):
        """Clear min. peak hold curve"""
        self.curve_peak_hold_min.clear()

    def clear_average(self):
        """Clear average curve"""
        self.curve_average.clear()

    def clear_baseline(self):
        """Clear baseline curve"""
        self.curve_baseline.clear()

    def clear_persistence(self):
        """Clear spectrum persistence curves"""
        self.persistence_data = None
        for curve in self.persistence_curves:
            curve.clear()
            self.plot.removeItem(curve)
        self.create_persistence_curves()


class WaterfallPlotWidget:
    """Waterfall plot"""
    def __init__(self, layout, histogram_layout=None):
        self.enabled = True
        if not isinstance(layout, pg.GraphicsLayoutWidget):
            raise ValueError("layout must be instance of pyqtgraph.GraphicsLayoutWidget")

        if histogram_layout and not isinstance(histogram_layout, pg.GraphicsLayoutWidget):
            raise ValueError("histogram_layout must be instance of pyqtgraph.GraphicsLayoutWidget")

        self.layout = layout
        self.histogram_layout = histogram_layout

        self.history_size = 100
        self.counter = 0
        self.visible_history_size = 0
        self.frequency_start = None
        self.frequency_stop = None
        self.snapshot_plot = None
        self.snapshot_image = None
        self.snapshot_frequency_range = None
        self.image_frequencies = None
        self.image_frequency_range = None

        self.create_plot()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.display_snapshot(None)
            if hasattr(self, "waterfallImg"):
                if self.histogram_layout:
                    try:
                        self.waterfallImg.sigImageChanged.disconnect(self.histogram.imageChanged)
                    except (TypeError, RuntimeError):
                        pass
                    self.histogram.imageItem = lambda: None
                    self.histogram.plot.setData([], [])
                self.plot.removeItem(self.waterfallImg)
                self.waterfallImg.clear()
                del self.waterfallImg
            self.clear_plot()

    def snapshot_data(self):
        if not self.enabled or self.visible_history_size == 0 or self.image_frequencies is None:
            raise ValueError('Enable the waterfall and collect data before capturing it')
        image = self.waterfallImg
        levels = image.getLevels()
        if levels is None:
            levels = [float(np.nanmin(image.image)), float(np.nanmax(image.image))]
        lut = self.histogram.getLookupTable(n=256, alpha=True)
        return dict(frequencies=self.image_frequencies.copy(), history=image.image.T.copy(),
                    frequency_range=list(self.image_frequency_range),
                    levels=np.asarray(levels).tolist(), lut=np.array(lut, dtype=np.uint8, copy=True))

    def display_snapshot(self, snapshot):
        if self.snapshot_plot is not None:
            self.snapshot_plot.setXLink(None)
            self.layout.removeItem(self.snapshot_plot)
            self.snapshot_plot = None
            self.snapshot_image = None
        self.snapshot_frequency_range = None
        if snapshot is None:
            return
        self.snapshot_frequency_range = snapshot['frequency_range']
        plot = self.layout.addPlot(row=1, col=0)
        self.snapshot_plot = plot
        plot.setLabel('bottom', 'Frequency', units='Hz')
        plot.setLabel('left', 'Saved sweeps')
        plot.getAxis('left').setWidth(80)
        plot.setTitle(snapshot.get('name') or 'Waterfall snapshot')
        plot.setMouseEnabled(y=False)
        image = pg.ImageItem(axisOrder='col-major', autoDownsample=True)
        self.snapshot_image = image
        image.setLookupTable(snapshot['lut'])
        pixels = snapshot.get('display_image')
        if pixels is None:
            pixels = snapshot['history']
            levels = snapshot['levels']
        else:
            levels = [0, 255]
        image.setImage(pixels.T, levels=levels, autoLevels=False)
        rows, bins = pixels.shape
        start, stop = snapshot['frequency_range']
        transform = QtGui.QTransform()
        transform.translate(start, -rows)
        transform.scale((stop - start) / bins, 1)
        image.setTransform(transform)
        plot.addItem(image)
        plot.setYRange(*snapshot['view_range'][1], padding=0)
        plot.setXLink(self.plot)

    def create_plot(self):
        """Create waterfall plot"""
        self.plot = self.layout.addPlot()
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLabel("left", "Time")

        self.lock_y_range()
        self.plot.setLimits(xMin=0)
        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
        self.plot.getViewBox().setMouseEnabled(y=False)
        self.plot.showButtons()
        #self.plot.setAspectLocked(True)

        #self.plot.setDownsampling(mode="peak")
        #self.plot.setClipToView(True)

        # Setup histogram widget (for controlling waterfall plot levels and gradients)
        if self.histogram_layout:
            self.histogram = pg.HistogramLUTItem()
            self.histogram_layout.addItem(self.histogram)
            self.histogram.gradient.loadPreset("flame")
            self.default_levels_state = self.histogram.saveState()
            #self.histogram.setHistogramRange(-50, 0)
            #self.histogram.setLevels(-50, 0)

    def reset_levels(self):
        """Restore the initial palette and automatic levels for current data."""
        if not self.histogram_layout:
            return
        self.histogram.restoreState(self.default_levels_state)
        self.histogram.imageChanged(autoLevel=True)
        self.histogram.autoHistogramRange()

    def lock_y_range(self):
        """Keep waterfall Y axis in history rows, never in power values."""
        history_size = max(1, self.history_size)
        self.plot.setLimits(
            yMin=-history_size,
            yMax=0,
            minYRange=history_size,
            maxYRange=history_size
        )
        self.plot.setYRange(-history_size, 0, padding=0)
        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)

    def set_frequency_range(self, start_freq, stop_freq):
        """Set configured waterfall frequency range in Hz."""
        self.frequency_start = min(start_freq, stop_freq)
        self.frequency_stop = max(start_freq, stop_freq)
        self.plot.setXRange(self.frequency_start, self.frequency_stop, padding=0)

    def get_frequency_rect(self, x, bins):
        """Return image X placement using configured range when available."""
        if self.frequency_start is not None and self.frequency_stop is not None:
            return self.frequency_start, self.frequency_stop - self.frequency_start

        if bins > 1:
            bin_width = (x[-1] - x[0]) / (bins - 1)
        else:
            bin_width = 1
        return x[0] - bin_width / 2, x[-1] - x[0] + bin_width

    def set_image_transform(self, freq_start, freq_width, visible_size, bins=None):
        """Place the waterfall image in plot coordinates without ImageItem.setRect."""
        if not hasattr(self, "waterfallImg"):
            return

        if bins is None:
            bins = self.waterfallImg.width()
        if bins is None or bins <= 0 or visible_size <= 0 or freq_width <= 0:
            return

        transform = QtGui.QTransform()
        transform.translate(freq_start, -visible_size)
        transform.scale(freq_width / bins, 1)
        self.waterfallImg.setTransform(transform)

    def update_plot(self, data_storage):
        """Update waterfall plot"""
        if not self.enabled:
            return
        self.counter += 1

        # Create waterfall image on first run
        if self.counter == 1:
            self.waterfallImg = pg.ImageItem(axisOrder="col-major")
            self.plot.clear()
            self.plot.addItem(self.waterfallImg)

        self.set_image_data(data_storage)

        # Link histogram widget to waterfall image on first run
        # (must be done after first data is received or else levels would be wrong)
        if self.counter == 1 and self.histogram_layout:
            self.histogram.setImageItem(self.waterfallImg)

    def clear_plot(self):
        """Clear waterfall plot"""
        self.counter = 0
        self.visible_history_size = 0
        self.image_frequencies = None
        self.image_frequency_range = None

    def recalculate_plot(self, data_storage):
        """Recalculate waterfall plot"""
        if not self.enabled or data_storage.x is None:
            return

        if not hasattr(self, "waterfallImg"):
            self.waterfallImg = pg.ImageItem(axisOrder="col-major")
            self.plot.clear()
            self.plot.addItem(self.waterfallImg)

        self.set_image_data(data_storage)
        if self.histogram_layout:
            self.histogram.setImageItem(self.waterfallImg)

    def set_image_data(self, data_storage):
        """Update waterfall image data and geometry"""
        if not self.enabled or data_storage.x is None or data_storage.history is None:
            return

        history = data_storage.history.get_buffer()
        if not len(history):
            return

        visible_size = min(len(history), self.history_size)
        history = history[-visible_size:]
        x = np.asarray(data_storage.x)
        bins = min(history.shape[1], len(x))
        if bins < 1:
            return
        if bins != history.shape[1] or bins != len(x):
            print("Waterfall data length mismatch: x={}, y={}; trimming to {}".format(
                len(x), history.shape[1], bins
            ))
            history = history[:, :bins]
            x = x[:bins]

        self.waterfallImg.setImage(history.T, autoLevels=False, autoRange=False)
        freq_start, freq_width = self.get_frequency_rect(x, bins)
        self.image_frequencies = x.copy()
        self.image_frequency_range = (freq_start, freq_start + freq_width)

        self.visible_history_size = visible_size
        self.set_image_transform(freq_start, freq_width, visible_size, bins)
        self.lock_y_range()

    def view_all(self, start_freq, stop_freq):
        """Show full configured waterfall frequency and history range"""
        if not self.enabled:
            return
        self.lock_y_range()
        visible_size = max(1, self.visible_history_size)
        if hasattr(self, "waterfallImg"):
            self.set_image_transform(start_freq, stop_freq - start_freq, visible_size)
        self.plot.setXRange(start_freq, stop_freq, padding=0)
        self.lock_y_range()
