import concurrent.futures
import json
from pathlib import Path
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
from Qt import QtCore, QtGui, QtWidgets
from qspectrumanalyzer.__main__ import QSpectrumAnalyzerMainWindow
from qspectrumanalyzer.mobile_server import render_images, MobileServerDialog


class MobileServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, self.temp.name)
        self.app.setOrganizationName('MobileServerTests')
        self.app.setApplicationName('Mobile')
        QtCore.QSettings().setValue('waterfall_history_size', 10)
        self.window = QSpectrumAnalyzerMainWindow()
        self.window.data_storage.data_updated.disconnect(self.window.update_data)
        self.window.recordingWidget.directoryEdit.setText(self.temp.name)
        self.window.recordingWidget.thresholdSpinBox.setValue(-80)
        self.window.actionWaterfall.setChecked(False)
        self.window.start_mobile_server('127.0.0.1', 0, 'test-token-1234567890')
        self.server = self.window.mobile_server
        self.url = 'http://127.0.0.1:{}'.format(self.server.http.server_port)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.settle()

    def tearDown(self):
        self.window.data_storage.wait()
        self.window.close()
        self.pool.shutdown(wait=True)
        self.app.processEvents()
        self.temp.cleanup()

    def settle(self):
        self.window.data_storage.wait()
        for _ in range(4):
            self.app.processEvents()

    def request(self, path, method='GET', token='test-token-1234567890', payload=None):
        def send():
            req = Request(self.url + path, method=method,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
            try:
                response = urlopen(req, timeout=7)
            except HTTPError as error:
                response = error
            with response:
                return response.status, dict(response.headers), response.read()
        future = self.pool.submit(send)
        deadline = time.monotonic()+8
        while not future.done() and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.005)
        return future.result(timeout=1)

    def feed(self, offset=0):
        storage = self.window.data_storage
        storage.set_frequency_range(100e6, 110e6)
        storage.update(dict(x=np.linspace(100e6,110e6,100),
                            y=np.arange(100.)-120+offset, timestamp=time.time()))
        self.settle()

    def test_auth_and_only_csv_commands_exposed(self):
        self.assertEqual(self.request('/api/v1/status',token='wrong')[0],401)
        self.assertEqual(self.request('/api/v1/recording/start','POST',token='wrong')[0],401)
        self.assertFalse(self.window.recordingWidget.recorder.active)
        self.assertEqual(self.request('/api/v1/scanning/start','POST')[0],400)
        self.assertEqual(self.request('/api/v1/status')[0],200)
        self.assertEqual(self.request('/api/v1/waterfall.png')[0],503)

    def test_dialog_shows_actual_running_endpoint_and_copyable_token(self):
        dialog = MobileServerDialog(self.window)
        self.assertEqual(dialog.host.text(), '127.0.0.1')
        self.assertEqual(dialog.port.value(), self.server.http.server_port)
        self.assertEqual(dialog.token.text(), self.server.token)
        self.assertTrue(dialog.token.isEnabled())
        self.assertTrue(dialog.token.isReadOnly())
        dialog.close()

    def test_recording_commands_are_idempotent_and_use_desktop_settings(self):
        code, _, body = self.request('/api/v1/recording/start','POST')
        self.assertEqual(code,200)
        self.assertTrue(json.loads(body)['recording']['active'])
        path = self.window.recordingWidget.recorder.path
        self.assertEqual(self.request('/api/v1/recording/start','POST')[0],200)
        self.assertEqual(self.window.recordingWidget.recorder.path,path)
        self.feed()
        self.assertGreater(self.window.recordingWidget.recorder.rows,0)
        self.assertEqual(self.request('/api/v1/recording/stop','POST')[0],200)
        self.assertEqual(self.request('/api/v1/recording/stop','POST')[0],200)
        self.assertFalse(self.window.recordingWidget.recorder.active)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.csv'))),1)
        self.assertIn(';signal;', path.read_text())

    def test_bad_recording_directory_returns_error_without_dialog(self):
        self.window.recordingWidget.directoryEdit.setText(str(Path(self.temp.name)/'missing'))
        code, _, body = self.request('/api/v1/recording/start','POST')
        self.assertEqual(code,409)
        self.assertIn('error',json.loads(body))
        self.assertFalse(self.window.recordingWidget.recorder.active)

    def test_images_and_history_work_with_desktop_curves_and_waterfall_hidden(self):
        self.assertFalse(self.window.waterfallPlotWidget.enabled)
        for i in range(3):
            self.feed(i)
        self.assertEqual(self.window.data_storage.history.history_size,3)
        self.assertFalse(hasattr(self.window.waterfallPlotWidget,'waterfallImg'))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            code, headers, body=self.request('/api/v1/waterfall.png')
            if code==200:
                break
            time.sleep(.02)
        self.assertEqual(code,200)
        self.assertEqual(headers['Content-Type'],'image/png')
        img=QtGui.QImage.fromData(body)
        self.assertEqual((img.width(),img.height()),(1024,420))
        code,_,body=self.request('/api/v1/spectrum.png')
        self.assertEqual(code,200)
        self.assertFalse(QtGui.QImage.fromData(body).isNull())
        self.window.stop_mobile_server()
        self.settle()
        self.assertEqual(self.window.data_storage.history.max_history_size,1)

    def test_stopping_server_does_not_stop_csv(self):
        self.request('/api/v1/recording/start','POST')
        self.window.stop_mobile_server()
        self.assertTrue(self.window.recordingWidget.recorder.active)

    def test_renderer_includes_both_curves_and_finite_fallback(self):
        x=np.arange(1000.)
        avg=np.full(1000,-90.)
        peak=avg.copy();peak[501]=-30
        images=render_images(x,avg,peak,np.stack([avg,peak]),(0,1000),None,
                             np.tile(np.arange(256,dtype=np.uint8)[:,None],(1,4)))
        image=QtGui.QImage.fromData(images['spectrum'])
        cyan=red=0
        for y in range(35,375):
            for x in range(70,1004):
                c=image.pixelColor(x,y)
                cyan+=c.green()>150 and c.blue()>150 and c.red()<30
                red+=c.red()>200 and c.green()<140 and c.blue()<140
        self.assertGreater(cyan,0)
        self.assertGreater(red,0)

    def test_waterfall_averages_bins_and_preserves_history_extent(self):
        # Ten bins per output column: one strong bin must not light the
        # entire column. Newest sweep is at the top, unused history is black.
        x = np.arange(9340.)
        older = np.full(len(x), -100.)
        newer = older.copy()
        newer[::10] = 0.
        lut = np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 4))
        images = render_images(x, older, newer, np.stack([older, newer]),
                               (0, len(x)), (-100., 0.), lut, history_size=4)
        image = QtGui.QImage.fromData(images['waterfall'])
        self.assertEqual(image.pixelColor(100, 50).red(), 25)
        self.assertEqual(image.pixelColor(100, 150).red(), 0)
        self.assertEqual(image.pixelColor(100, 300).red(), 0)

    def test_waterfall_limits_display_to_configured_history(self):
        x = np.arange(934.)
        history = np.stack([np.full(len(x), v) for v in (-100., -50., 0.)])
        lut = np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 4))
        image = QtGui.QImage.fromData(render_images(
            x, history[0], history[-1], history, (0, len(x)),
            (-100., 0.), lut, history_size=2)['waterfall'])
        self.assertEqual(image.pixelColor(100, 50).red(), 255)
        self.assertEqual(image.pixelColor(100, 300).red(), 127)

    def test_remote_snapshot_saves_pc_pair_name_and_serves_saved_png(self):
        from qspectrumanalyzer.snapshots import read_snapshot
        self.window.snapshotWidget.directoryEdit.setText(self.temp.name)
        self.window.actionWaterfall.setChecked(True)
        self.feed()
        self.window.spectrumPlotWidget.update_plot(self.window.data_storage)
        self.window.waterfallPlotWidget.update_plot(self.window.data_storage)
        for kind in ('spectrum', 'waterfall'):
            code, _, body = self.request('/api/v1/snapshots', 'POST',
                                         payload={'kind': kind, 'name': 'Mobile capture'})
            self.assertEqual(code, 200, body)
            job = json.loads(body)
            deadline = time.monotonic() + 5
            while job['state'] == 'saving' and time.monotonic() < deadline:
                _, _, body = self.request('/api/v1/snapshot-jobs/' + job['id'])
                job = json.loads(body)
            self.assertEqual(job['state'], 'saved', job)
            path = Path(self.temp.name) / (job['snapshot_id'] + '.npz')
            metadata = read_snapshot(path)
            self.assertEqual(metadata['name'], 'Mobile capture')
            self.assertEqual(metadata.get('kind', 'spectrum'), kind)
            if kind == 'waterfall':
                self.assertGreater(metadata['history'].size, 0)
            else:
                self.assertGreater(metadata['curves'][0]['x'].size, 0)
            code, _, png = self.request('/api/v1/snapshots/' + job['snapshot_id'] + '.png')
            self.assertEqual(code, 200)
            self.assertEqual(png, path.with_suffix('.png').read_bytes())
        code, _, body = self.request('/api/v1/snapshots')
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)['snapshots']), 2)
        self.assertEqual(self.window.snapshotWidget.table.rowCount(), 2)

    def test_remote_snapshot_rejects_invalid_type_and_hidden_waterfall(self):
        self.assertEqual(self.request('/api/v1/snapshots', 'POST',
                         payload={'kind': 'invalid'})[0], 400)
        self.assertEqual(self.request('/api/v1/snapshots', 'POST',
                         payload={'kind': 'spectrum'}, token='wrong')[0], 401)
        self.assertEqual(self.request('/api/v1/snapshots', 'POST',
                         payload={'kind': 'waterfall'})[0], 409)
        self.assertEqual(self.request('/api/v1/snapshots/../../secret.png')[0], 404)

    def test_remote_snapshot_reports_disk_failure_without_modal(self):
        self.feed()
        self.window.spectrumPlotWidget.update_plot(self.window.data_storage)
        self.window.snapshotWidget.directoryEdit.setText(str(Path(self.temp.name) / 'missing'))
        code, _, body = self.request('/api/v1/snapshots', 'POST', payload={'kind': 'spectrum'})
        self.assertEqual(code, 200)
        job = json.loads(body)
        deadline = time.monotonic() + 5
        while job['state'] == 'saving' and time.monotonic() < deadline:
            _, _, body = self.request('/api/v1/snapshot-jobs/' + job['id'])
            job = json.loads(body)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('directory', job['error'])
        self.assertFalse(list(Path(self.temp.name).glob('*.npz')))

    def test_mobile_frequency_validation_start_and_stop(self):
        from unittest.mock import patch
        self.window.startFreqSpinBox.setRange(1, 6000)
        self.window.stopFreqSpinBox.setRange(1, 6000)
        original = self.window.startFreqSpinBox.value()
        for payload in ({'start_mhz': 200, 'stop_mhz': 100},
                        {'start_mhz': -1, 'stop_mhz': 100},
                        {'start_mhz': float('nan'), 'stop_mhz': 100}):
            self.assertEqual(self.request('/api/v1/scanning/frequency', 'POST', payload=payload)[0], 409)
            self.assertEqual(self.window.startFreqSpinBox.value(), original)
        payload = {'start_mhz': 100, 'stop_mhz': 110}
        code, _, body = self.request('/api/v1/scanning/frequency', 'POST', payload=payload)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['configured_frequency_mhz'], [100, 110])
        self.assertEqual(QtCore.QSettings().value('start_freq', type=float), 100)
        with patch.object(self.window, 'start') as start:
            self.assertEqual(self.request('/api/v1/scanning/start', 'POST', payload=payload)[0], 200)
            start.assert_called_once_with(remote=True)
        with patch.object(self.window, 'stop') as stop:
            self.assertEqual(self.request('/api/v1/scanning/stop', 'POST')[0], 200)
            stop.assert_called_once()
        self.window.power_thread.alive = True
        try:
            self.assertEqual(self.request('/api/v1/scanning/frequency', 'POST', payload=payload)[0], 409)
        finally:
            self.window.power_thread.alive = False
        self.assertEqual(self.request('/api/v1/scanning/start', 'POST', payload=payload, token='wrong')[0], 401)

    def test_remote_start_missing_backend_returns_error_without_modal(self):
        QtCore.QSettings().setValue('executable', '/missing/qspectrum-backend')
        code, _, body = self.request('/api/v1/scanning/start', 'POST',
                                     payload={'start_mhz': 100, 'stop_mhz': 110})
        self.assertEqual(code, 409)
        self.assertIn('Backend executable not found', json.loads(body)['error'])

    def test_token_rotation_persists_and_revokes_old_token(self):
        dialog = MobileServerDialog(self.window)
        previous = self.server.token
        dialog.rotate_token()
        token = dialog.token.text()
        self.assertNotEqual(token, previous)
        self.assertEqual(self.request('/api/v1/status', token=previous)[0], 401)
        self.assertEqual(self.request('/api/v1/status', token=token)[0], 200)
        self.assertEqual(QtCore.QSettings().value('mobile/token'), token)
        dialog.close()
        self.window.stop_mobile_server()
        reopened = MobileServerDialog(self.window)
        self.assertEqual(reopened.token.text(), token)
        self.assertTrue(reopened.token.isReadOnly())
        reopened.close()

    def test_initial_token_is_persisted_without_starting_server(self):
        self.window.stop_mobile_server()
        QtCore.QSettings().remove('mobile/token')
        first = MobileServerDialog(self.window)
        token = first.token.text()
        first.close()
        second = MobileServerDialog(self.window)
        self.assertEqual(second.token.text(), token)
        self.assertGreaterEqual(len(token), 16)
        second.close()

    def test_dialog_shows_lan_addresses_and_port(self):
        from unittest.mock import patch
        from Qt import QtNetwork
        self.window.stop_mobile_server()
        with patch.object(QtNetwork.QNetworkInterface, 'allAddresses', return_value=[
                QtNetwork.QHostAddress('127.0.0.1'),
                QtNetwork.QHostAddress('192.168.1.117'),
                QtNetwork.QHostAddress('::1')]):
            dialog = MobileServerDialog(self.window)
            dialog.host.setText('0.0.0.0')
            dialog.port.setValue(8766)
            self.assertIn('http://192.168.1.117:8766', dialog.addresses.text())
            self.assertNotIn('127.0.0.1', dialog.addresses.text())
            dialog.close()
