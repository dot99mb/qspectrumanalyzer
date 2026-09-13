"""Authenticated image feed, CSV controls and desktop snapshot commands."""
import concurrent.futures
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid
from .snapshots import read_snapshot
import queue
import secrets
import threading
import time

import numpy as np
from Qt import QtCore, QtGui, QtWidgets, QtNetwork


class ImageHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, controller):
        self.controller = controller
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, RequestHandler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class RequestHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # Do not log credentials or request URLs.

    def reply(self, code, data, content_type='application/json', frame=None):
        if content_type == 'application/json':
            data = json.dumps(data, allow_nan=False).encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            if frame is not None:
                self.send_header('X-Frame-Id', str(frame))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, TimeoutError):
            pass

    def authorized(self):
        with self.server.controller.lock:
            expected = 'Bearer ' + self.server.controller.token
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            self.reply(401, {'error': 'Bearer token required'})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        c = self.server.controller
        if self.path == '/api/v1/status':
            with c.lock:
                state = dict(c.state)
            self.reply(200, state)
            return
        if self.path == '/api/v1/snapshots':
            with c.lock:
                directory = c.snapshot_directory
            entries = []
            if not directory:
                self.reply(200, {'snapshots': []})
                return
            try:
                for path in sorted(Path(directory).glob('spectrum_*.npz'), reverse=True):
                    try:
                        metadata = read_snapshot(path, metadata_only=True)
                        if path.with_suffix('.png').is_file():
                            entries.append(dict(id=path.stem, name=metadata.get('name', ''),
                                                created=metadata['created'], kind=metadata.get('kind', 'spectrum'),
                                                frequency_range=metadata.get('frequency_range', metadata['view_range'][0])))
                    except Exception:
                        continue
                self.reply(200, {'snapshots': entries})
            except OSError as error:
                self.reply(409, {'error': str(error)})
            return
        match = re.fullmatch(r'/api/v1/snapshots/(spectrum_[A-Za-z0-9_]+)\.png', self.path)
        if match:
            with c.lock:
                directory = c.snapshot_directory
            try:
                if not directory:
                    raise FileNotFoundError()
                path = Path(directory) / (match[1] + '.png')
                if not path.with_suffix('.npz').is_file():
                    raise FileNotFoundError()
                self.reply(200, path.read_bytes(), 'image/png')
            except OSError:
                self.reply(404, {'error': 'Snapshot not found'})
            return
        match = re.fullmatch(r'/api/v1/snapshot-jobs/([a-f0-9]{32})', self.path)
        if match:
            with c.lock:
                job = c.snapshot_jobs.get(match[1])
            self.reply(200 if job else 404, job or {'error': 'Job not found'})
            return
        with c.lock:
            key = {'/api/v1/spectrum.png': 'spectrum', '/api/v1/waterfall.png': 'waterfall'}.get(self.path)
            c.last_image_request = time.monotonic()
            image = c.images.get(key)
            frame = c.frame_id
        if key is None:
            self.reply(404, {'error': 'Unknown endpoint'})
            return
        if image is None:
            self.reply(503, {'error': 'Image not ready; start scanning on the desktop and retry'})
        else:
            self.reply(200, image, 'image/png', frame)

    def do_POST(self):
        if not self.authorized():
            return
        if self.path in ('/api/v1/snapshots', '/api/v1/scanning/start', '/api/v1/scanning/frequency'):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < length <= 4096:
                    raise ValueError('Invalid request length')
                payload = json.loads(self.rfile.read(length))
                if self.path != '/api/v1/snapshots':
                    if not isinstance(payload, dict):
                        raise ValueError('Expected JSON object')
                    action = (self.path.rsplit('/', 1)[1], payload)
                elif payload.get('kind') not in ('spectrum', 'waterfall'):
                    raise ValueError('Choose spectrum or waterfall')
                if not isinstance(payload.get('name', ''), str) or len(payload.get('name', '')) > 100:
                    raise ValueError('Invalid snapshot name')
                if self.path == '/api/v1/snapshots':
                    action = ('snapshot', payload)
            except (ValueError, AttributeError, OSError) as error:
                self.reply(400, {'error': str(error)})
                return
        else:
            action = {'/api/v1/recording/start' : 'start', '/api/v1/recording/stop': 'stop', '/api/v1/scanning/stop': 'scan_stop'}.get(self.path)
        if action is None:
            self.reply(404, {'error': 'Unknown endpoint'})
            return
        if not isinstance(action, tuple) and (self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0'):
            self.reply(400, {'error': 'Send an empty request body; settings are configured on the desktop'})
            return
        future = concurrent.futures.Future()
        try:
            self.server.controller.commands.put_nowait((action, future))
        except queue.Full:
            self.reply(503, {'error': 'Command queue full'})
            return
        try:
            result = future.result(timeout=5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            self.reply(504, {'error': 'Command timed out; check the recording state or snapshot list before retrying'})
        else:
            self.reply(200 if 'error' not in result else 409, result)


def png_bytes(image):
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise OSError('PNG encoding failed')
    return bytes(data)


def render_images(x, average, maximum, history, frequency_range, levels, lut, history_size=None):
    """Paint off the UI thread: spectrum extrema, mean waterfall power per column."""
    width, height = 1024, 420
    left, top, right, bottom = 70, 35, 20, 45
    columns = width - left - right
    bins = len(x)
    edges = np.linspace(0, bins, min(columns, bins) + 1, dtype=int)
    starts = edges[:-1]
    count = len(starts)
    reduced = []
    for values in (average, maximum):
        values = np.asarray(values)
        low = np.minimum.reduceat(values, starts)
        high = np.maximum.reduceat(values, starts)
        reduced.append((low, high))
    finite = np.concatenate([v[np.isfinite(v)] for pair in reduced for v in pair])
    ylow, yhigh = (float(finite.min()), float(finite.max())) if finite.size else (-120., 0.)
    padding = max(2., (yhigh - ylow) * .05)
    ylow -= padding
    yhigh += padding
    start, stop = frequency_range

    def base(title, ymin, ymax, ylabel):
        image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor('#080b13'))
        painter = QtGui.QPainter(image)
        painter.setFont(QtGui.QFont('DejaVu Sans', 9))
        painter.setPen(QtGui.QColor('#cccccc'))
        painter.drawText(left, 20, title)
        for i in range(6):
            px = left + round(columns * i / 5)
            py = top + round((height - top - bottom) * i / 5)
            painter.setPen(QtGui.QColor('#303642'))
            painter.drawLine(px, top, px, height - bottom)
            painter.drawLine(left, py, width - right, py)
            painter.setPen(QtGui.QColor('#cccccc'))
            painter.drawText(px - 24, height - 25, '{:.2f}'.format((start + (stop - start) * i / 5) / 1e6))
            painter.drawText(5, py + 4, '{:.1f}'.format(ymax - (ymax - ymin) * i / 5))
        painter.drawText(width // 2 - 40, height - 5, 'Frequency (MHz)')
        painter.drawText(5, 20, ylabel)
        return image, painter

    spectrum, painter = base('Average (cyan) / Max hold (red)', ylow, yhigh, 'dB')
    for color, (low, high) in zip(('#00dddd', '#ff6060'), reduced):
        painter.setPen(QtGui.QPen(QtGui.QColor(color), 1))
        polygon = QtGui.QPolygonF()
        for i in range(count):
            px = left + (i + .5) * columns / count
            for value in (low[i], high[i]):
                if np.isfinite(value):
                    py = top + (yhigh - value) * (height - top - bottom) / (yhigh - ylow)
                    polygon.append(QtCore.QPointF(px, py))
        painter.drawPolyline(polygon)
    painter.end()
    history_size = max(1, int(history_size or len(history)))
    history = history[-history_size:]
    waterfall, painter = base('Waterfall — newest sweep at top', -history_size, 0, 'Sweeps')
    # ImageItem averages power when downsampling the desktop waterfall.
    # Max pooling here instead exaggerates sparse peaks across the whole band.
    power = (np.add.reduceat(history, starts, axis=1, dtype=np.float64)
             / np.diff(edges))[::-1]
    if levels is None:
        valid = power[np.isfinite(power)]
        levels = (float(valid.min()), float(valid.max())) if valid.size else (-120., 0.)
    lo, hi = levels
    normalized = (power - lo) / (hi - lo) if hi > lo else np.zeros_like(power)
    pixels = np.nan_to_num(normalized * 255., nan=0., posinf=255., neginf=0.)
    pixels = np.clip(pixels, 0, 255).astype(np.uint8)
    rgb = np.ascontiguousarray(lut[pixels, :3])
    image = QtGui.QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QtGui.QImage.Format_RGB888)
    painter.fillRect(QtCore.QRectF(left, top, columns, height - top - bottom), QtGui.QColor('black'))
    visible_height = (height - top - bottom) * len(history) / history_size
    painter.drawImage(QtCore.QRectF(left, top, columns, visible_height), image)
    painter.end()
    return {'spectrum': png_bytes(spectrum), 'waterfall': png_bytes(waterfall)}


class MobileServer(QtCore.QObject):
    def __init__(self, window, host, port, token):
        super().__init__(window)
        if len(token) < 16:
            raise ValueError('Use an access token of at least 16 characters')
        self.window, self.token = window, token
        self.lock = threading.Lock()
        self.commands = queue.Queue(maxsize=16)
        self.images, self.state = {}, {}
        self.snapshot_jobs = {}
        self.snapshot_directory = ""
        self.frame_id = 0
        self.image_time = None
        self.last_image_request = 0.
        self.last_render = 0.
        self.future = None
        self.source = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='mobile-images')
        try:
            self.http = ImageHTTPServer((host, port), self)
        except Exception:
            self.executor.shutdown(wait=False)
            raise
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.tick()
        self.thread.start()
        self.timer.start()

    def status(self):
        w = self.window
        r = w.recordingWidget.recorder
        return dict(api_version=1, scanning=bool(w.power_thread and (w.power_thread.alive or w.power_thread.isRunning())),
                    recording=dict(active=r.active, filename=r.path.name if getattr(r, 'path', None) else None,
                                   sweeps=r.sweeps, signals=r.rows,
                                   threshold_db=w.recordingWidget.thresholdSpinBox.value(),
                                   separator=w.recordingWidget.delimiterEdit.text(),
                                   message=w.recordingWidget.statusLabel.text()),
                    frame_id=self.frame_id, server_time=time.time(), image_width=1024,
                    image_height=420, max_image_fps=2, image_time=self.image_time,
                    last_data_time=w.prev_data_timestamp,
                    frequency_range_hz=[w.data_storage.frequency_start, w.data_storage.frequency_stop],
                    configured_frequency_mhz=[w.startFreqSpinBox.value(), w.stopFreqSpinBox.value()],
                    frequency_limits_mhz=[[w.startFreqSpinBox.minimum(), w.startFreqSpinBox.maximum()],
                                          [w.stopFreqSpinBox.minimum(), w.stopFreqSpinBox.maximum()]])

    def recording_command(self, action):
        panel = self.window.recordingWidget
        recorder = panel.recorder
        try:
            if action == 'start' and not recorder.active:
                recorder.start(panel.directoryEdit.text(), panel.thresholdSpinBox.value(), panel.delimiterEdit.text())
                panel.save_settings()
                panel.statusLabel.setText('Recording: {} — started from mobile client'.format(recorder.path.name))
                panel.statusLabel.setToolTip(str(recorder.path))
            elif action == 'stop':
                recorder.stop()
                panel.statusLabel.setText('Recording stopped — sweeps: {} | signals: {}'.format(recorder.sweeps, recorder.rows))
            panel.update_controls()
            return self.status()
        except Exception as error:
            panel.update_controls()
            return {'error': str(error)}

    def scanning_command(self, action, payload=None):
        w = self.window
        try:
            if action == 'scan_stop':
                w.stop()
                return self.status()
            if w.power_thread.alive or w.power_thread.isRunning():
                raise ValueError('Stop scanning before changing the frequency range')
            payload = payload or {}
            start, stop = payload.get('start_mhz'), payload.get('stop_mhz')
            if (type(start) not in (int, float) or type(stop) not in (int, float)
                    or not np.isfinite([start, stop]).all() or start >= stop):
                raise ValueError('Specify finite frequencies with start < stop (MHz)')
            if not (w.startFreqSpinBox.minimum() <= start <= w.startFreqSpinBox.maximum()
                    and w.stopFreqSpinBox.minimum() <= stop <= w.stopFreqSpinBox.maximum()):
                raise ValueError('Frequency range is outside the selected backend limits')
            if round(start, w.startFreqSpinBox.decimals()) >= round(stop, w.stopFreqSpinBox.decimals()):
                raise ValueError('Frequency range is smaller than supported precision')
            for widget, value in ((w.startFreqSpinBox, start), (w.stopFreqSpinBox, stop)):
                blocker = QtCore.QSignalBlocker(widget)
                widget.setValue(value)
                del blocker
            w.fit_frequency_range()
            settings = QtCore.QSettings()
            settings.setValue('start_freq', w.startFreqSpinBox.value())
            settings.setValue('stop_freq', w.stopFreqSpinBox.value())
            if action == 'start':
                w.start(remote=True)
            return self.status()
        except Exception as error:
            return {'error': str(error)}

    def tick(self):
        while True:
            try:
                action, result = self.commands.get_nowait()
            except queue.Empty:
                break
            if result.set_running_or_notify_cancel():
                if action == 'scan_stop':
                    result.set_result(self.scanning_command(action))
                elif isinstance(action, tuple) and action[0] != 'snapshot':
                    result.set_result(self.scanning_command(*action))
                elif isinstance(action, tuple):
                    try:
                        payload = action[1]
                        worker = self.window.capture_snapshot(kind=payload['kind'], name=payload.get('name', ''), remote=True)
                        job_id = uuid.uuid4().hex
                        with self.lock:
                            if len(self.snapshot_jobs) >= 32:
                                self.snapshot_jobs.pop(next(iter(self.snapshot_jobs)))
                            self.snapshot_jobs[job_id] = {'id': job_id, 'state': 'saving'}
                        def finished(worker=worker, job_id=job_id):
                            with self.lock:
                                self.snapshot_jobs[job_id] = (
                                    {'id': job_id, 'state': 'failed', 'error': worker.error}
                                    if worker.error else
                                    {'id': job_id, 'state': 'saved', 'snapshot_id': worker.path.stem})
                        worker.finished.connect(finished)
                        result.set_result({'id': job_id, 'state': 'saving'})
                    except Exception as error:
                        result.set_result({'error': str(error)})
                else:
                    result.set_result(self.recording_command(action))
        storage = self.window.data_storage
        source = (id(storage), id(storage.x)) if storage is not None else None
        if source != self.source:
            self.source = source
            with self.lock:
                self.images = {}
                self.image_time = None
        if self.future is not None and self.future.done():
            try:
                images = self.future.result()
                if self.future.source == self.source:
                    with self.lock:
                        self.images = images
                        self.frame_id += 1
                        self.image_time = time.time()
            except Exception as error:
                with self.lock:
                    self.images = {}
                self.window.show_status('Mobile image error: {}'.format(error))
            self.future = None
        with self.lock:
            self.state = self.status()
            directory = self.window.snapshotWidget.directoryEdit.text().strip()
            self.snapshot_directory = str(Path(directory).expanduser().resolve()) if directory else ''
            wanted = time.monotonic() - self.last_image_request < 3
        if not wanted or self.future is not None or time.monotonic() - self.last_render < .5:
            return
        if storage is None or storage.x is None or storage.history is None:
            return
        x, avg, maximum = storage.x, storage.average, storage.peak_hold_max
        history = storage.history.get_buffer()
        if storage.history.max_history_size == 1:
            history = history.copy()
        if avg is None or maximum is None or not len(history) or len(x) < 2:
            return
        if len(avg) != len(x) or len(maximum) != len(x) or history.shape[1] != len(x):
            return
        # Storage replaces arrays on updates. Keeping these references gives the
        # renderer stable data without copying the full history on the UI thread.
        wf = self.window.waterfallPlotWidget
        lut = np.array(wf.histogram.getLookupTable(n=256, alpha=True), copy=True)
        levels = wf.histogram.getLevels() if wf.enabled and wf.visible_history_size else None
        start = storage.frequency_start if storage.frequency_start is not None else float(x[0])
        stop = storage.frequency_stop if storage.frequency_stop is not None else float(x[-1])
        self.future = self.executor.submit(render_images, x, avg, maximum, history, (start, stop), levels, lut, wf.history_size)
        self.future.source = self.source
        self.last_render = time.monotonic()

    def stop(self):
        self.timer.stop()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=1)
        self.executor.shutdown(wait=True)
        while not self.commands.empty():
            _, result = self.commands.get_nowait()
            if result.set_running_or_notify_cancel():
                result.set_result({'error': 'Server stopped'})
        with self.lock:
            self.images = {}


class MobileServerDialog(QtWidgets.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Mobile server')
        settings = QtCore.QSettings()
        layout = QtWidgets.QFormLayout(self)
        self.host = QtWidgets.QLineEdit(settings.value('mobile/host', '0.0.0.0'))
        self.port = QtWidgets.QSpinBox(); self.port.setRange(1, 65535)
        self.port.setValue(settings.value('mobile/port', 8765, int))
        self.token = QtWidgets.QLineEdit(settings.value('mobile/token', '') or secrets.token_urlsafe(32))
        if window.mobile_server is not None:
            self.host.setText(window.mobile_server.http.server_address[0])
            self.port.setValue(window.mobile_server.http.server_port)
            self.token.setText(window.mobile_server.token)
        settings.setValue('mobile/token', self.token.text())
        self.token.setReadOnly(True)
        layout.addRow('Listen address:', self.host)
        layout.addRow('Port:', self.port)
        layout.addRow('Access token:', self.token)
        self.rotate_button = QtWidgets.QPushButton('Generate new token')
        self.rotate_button.clicked.connect(self.rotate_token)
        layout.addRow(self.rotate_button)
        self.addresses = QtWidgets.QLabel()
        self.addresses.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow('Current PC addresses:', self.addresses)
        self.address_timer = QtCore.QTimer(self)
        self.address_timer.setInterval(2000)
        self.address_timer.timeout.connect(self.refresh_addresses)
        self.port.valueChanged.connect(self.refresh_addresses)
        self.host.textChanged.connect(self.refresh_addresses)
        note = QtWidgets.QLabel('Use the computer’s LAN IP on the phone.\nHTTP: use a trusted LAN or VPN. Send Authorization: Bearer <token>.')
        layout.addRow(note)
        self.state = QtWidgets.QLabel()
        layout.addRow(self.state)
        self.start_button = QtWidgets.QPushButton('Start server')
        self.stop_button = QtWidgets.QPushButton('Stop server')
        layout.addRow(self.start_button, self.stop_button)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh()

    def refresh(self):
        active = self.window.mobile_server is not None
        for widget in (self.host, self.port, self.start_button):
            widget.setEnabled(not active)
        self.token.setReadOnly(True)
        self.stop_button.setEnabled(active)
        self.refresh_addresses()
        self.state.setText('Running on port {}'.format(self.window.mobile_server.http.server_port) if active else 'Stopped')

    def rotate_token(self):
        token = secrets.token_urlsafe(32)
        server = self.window.mobile_server
        if server is not None:
            with server.lock:
                server.token = token
        QtCore.QSettings().setValue('mobile/token', token)
        self.token.setText(token)

    def refresh_addresses(self, *args):
        server = self.window.mobile_server
        host = server.http.server_address[0] if server else self.host.text().strip()
        port = server.http.server_port if server else self.port.value()
        addresses = sorted({address.toString() for address in QtNetwork.QNetworkInterface.allAddresses()
                            if address.protocol() == QtNetwork.QAbstractSocket.IPv4Protocol
                            and not address.isLoopback()})
        if host in ('127.0.0.1', 'localhost'):
            text = 'http://127.0.0.1:{} (PC only; use 0.0.0.0 for phone access)'.format(port)
        elif host == '0.0.0.0':
            text = '\n'.join('http://{}:{}'.format(address, port) for address in addresses)
            text = text or 'No LAN IPv4 address. Connect the PC to a network.'
        else:
            text = 'http://{}:{}'.format(host, port)
        self.addresses.setText(text)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_addresses()
        self.address_timer.start()

    def hideEvent(self, event):
        self.address_timer.stop()
        super().hideEvent(event)

    def start_server(self):
        try:
            self.window.start_mobile_server(self.host.text().strip(), self.port.value(), self.token.text().strip())
        except (OSError, ValueError) as error:
            QtWidgets.QMessageBox.warning(self, 'Mobile server', str(error))
            return
        settings = QtCore.QSettings()
        for key, value in [('host', self.host.text().strip()), ('port', self.port.value()), ('token', self.token.text().strip())]:
            settings.setValue('mobile/' + key, value)
        self.refresh()

    def stop_server(self):
        self.window.stop_mobile_server()
        self.refresh()
