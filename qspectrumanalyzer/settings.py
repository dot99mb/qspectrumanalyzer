from qspectrumanalyzer.qt import QtCore, QtGui, QtWidgets

from qspectrumanalyzer import backends
from qspectrumanalyzer.utils import (
    executable_available,
    executable_matches_backend,
    find_backend_executable,
)

from qspectrumanalyzer.ui_qspectrumanalyzer_settings import Ui_QSpectrumAnalyzerSettings
from qspectrumanalyzer.ui_qspectrumanalyzer_settings_help import Ui_QSpectrumAnalyzerSettingsHelp


def ensure_backend_settings_consistent(settings=None):
    """Repair legacy settings that mix values from two different backends."""
    settings = settings or QtCore.QSettings()
    backend = settings.value("backend", "soapy_power")
    if backend not in backends.__all__:
        backend = "soapy_power"
        settings.setValue("backend", backend)

    executable = settings.value("executable", "")
    if (settings.value("settings_backend", "") == backend or
            executable_matches_backend(executable, backend)):
        resolved = find_backend_executable(backend)
        if resolved and str(executable).lower() in (backend.lower(), (backend + ".exe").lower()):
            settings.setValue("executable", resolved)
        settings.setValue("settings_backend", backend)
        settings.sync()
        return backend

    backend_module = getattr(backends, backend)
    prefix = "backend_profiles/{}/".format(backend)
    default_device = "0" if backend in ("rtl_power", "rtl_power_fftw") else ""
    settings.setValue(
        "executable",
        settings.value(prefix + "executable", find_backend_executable(backend) or backend),
    )
    settings.setValue("params", settings.value(prefix + "params", backend_module.Info.additional_params))
    settings.setValue("device", settings.value(prefix + "device", default_device))
    settings.setValue("sample_rate", settings.value(prefix + "sample_rate", backend_module.Info.sample_rate, float))
    settings.setValue("bandwidth", settings.value(prefix + "bandwidth", backend_module.Info.bandwidth, float))
    settings.setValue("settings_backend", backend)
    settings.sync()
    return backend


class QSpectrumAnalyzerSettings(QtWidgets.QDialog, Ui_QSpectrumAnalyzerSettings):
    """QSpectrumAnalyzer settings dialog"""
    def __init__(self, parent=None):
        # Initialize UI
        super().__init__(parent)
        self.setupUi(self)
        self.params_help_dialog = None
        self.device_help_dialog = None
        self.testBackendButton = self.buttonBox.addButton(
            self.tr("Test backend"), QtWidgets.QDialogButtonBox.ActionRole
        )
        self.testBackendButton.clicked.connect(self.test_backend)
        self.rtlSdrButton = self.buttonBox.addButton(
            self.tr("RTL-SDR preset"), QtWidgets.QDialogButtonBox.ActionRole
        )
        self.rtlSdrButton.setToolTip(
            self.tr("Configure rtl_power with safe defaults for an RTL-SDR dongle")
        )
        self.rtlSdrButton.clicked.connect(self.apply_rtl_sdr_preset)

        # Load settings
        settings = QtCore.QSettings()
        self.lnbSpinBox.setValue(settings.value("lnb_lo", 0, float) / 1e6)
        self.waterfallHistorySizeSpinBox.setValue(settings.value("waterfall_history_size", 100, int))

        backend = settings.value("backend", "soapy_power")
        if backend not in backends.__all__:
            backend = "soapy_power"

        self.backendComboBox.blockSignals(True)
        self.backendComboBox.clear()
        for b in sorted(backends.__all__):
            self.backendComboBox.addItem(b)

        i = self.backendComboBox.findText(backend)
        if i == -1:
            self.backendComboBox.setCurrentIndex(0)
        else:
            self.backendComboBox.setCurrentIndex(i)
        self.backendComboBox.blockSignals(False)
        self.load_backend_profile(backend, allow_legacy=True)

    @staticmethod
    def backend_module(backend):
        """Return backend module, falling back to soapy_power."""
        try:
            return getattr(backends, backend)
        except AttributeError:
            return backends.soapy_power

    def load_backend_profile(self, backend, allow_legacy=False):
        """Load settings belonging only to the selected backend."""
        settings = QtCore.QSettings()
        backend_module = self.backend_module(backend)
        info = backend_module.Info
        prefix = "backend_profiles/{}/".format(backend)
        default_executable = find_backend_executable(backend) or backend
        default_device = "0" if backend in ("rtl_power", "rtl_power_fftw") else ""

        has_profile = settings.contains(prefix + "executable")
        legacy_executable = settings.value("executable", "")
        legacy_backend = settings.value("settings_backend", "")
        use_legacy = allow_legacy and not has_profile and (
            legacy_backend == backend or executable_matches_backend(legacy_executable, backend)
        )

        def profile_value(name, default, value_type=None):
            if has_profile:
                return settings.value(prefix + name, default, value_type) if value_type else settings.value(prefix + name, default)
            if use_legacy:
                return settings.value(name, default, value_type) if value_type else settings.value(name, default)
            return default

        self.executableEdit.setText(profile_value("executable", default_executable))
        self.paramsEdit.setText(profile_value("params", info.additional_params))
        self.deviceEdit.setText(profile_value("device", default_device))
        self.deviceHelpButton.setEnabled(bool(info.help_device))

        self.sampleRateSpinBox.setMinimum(info.sample_rate_min / 1e6)
        self.sampleRateSpinBox.setMaximum(info.sample_rate_max / 1e6)
        self.sampleRateSpinBox.setValue(
            profile_value("sample_rate", info.sample_rate, float) / 1e6
        )
        self.sampleRateSpinBox.setEnabled(info.sample_rate_max > 0)
        self.bandwidthSpinBox.setMinimum(info.bandwidth_min / 1e6)
        self.bandwidthSpinBox.setMaximum(info.bandwidth_max / 1e6)
        self.bandwidthSpinBox.setValue(
            profile_value("bandwidth", info.bandwidth, float) / 1e6
        )

    @QtCore.Slot()
    def on_executableButton_clicked(self):
        """Open file dialog when button is clicked"""
        filename = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.tr("Select executable - QSpectrumAnalyzer"),
            "",
            self.tr("Programs (*.exe *.bat *.cmd);;All files (*)"),
        )[0]
        if filename:
            self.executableEdit.setText(filename)

    @QtCore.Slot()
    def test_backend(self):
        """Check that the configured backend command can be resolved."""
        executable = self.executableEdit.text()
        if executable_available(executable):
            QtWidgets.QMessageBox.information(
                self,
                self.tr("Backend found"),
                self.tr("The backend executable is available:\n\n{}").format(executable),
            )
        else:
            backend = self.backendComboBox.currentText()
            hint = ""
            if backend in ("rtl_power", "rtl_power_fftw"):
                hint = self.tr(
                    "\n\nInstall the RTL-SDR command-line tools and select rtl_power.exe. "
                    "The RTL-SDR USB interface must use the WinUSB driver."
                )
            QtWidgets.QMessageBox.critical(
                self,
                self.tr("Backend not found"),
                self.tr("The backend executable could not be found:\n\n{}").format(executable) + hint,
            )

    @QtCore.Slot()
    def apply_rtl_sdr_preset(self):
        """Apply known-good starting values for a common RTL-SDR dongle."""
        index = self.backendComboBox.findText("rtl_power")
        if index >= 0:
            self.backendComboBox.setCurrentIndex(index)
        self.executableEdit.setText(find_backend_executable("rtl_power") or "rtl_power")
        self.paramsEdit.clear()
        self.deviceEdit.setText("0")
        self.sampleRateSpinBox.setValue(0)
        self.bandwidthSpinBox.setValue(0)

    @QtCore.Slot()
    def on_paramsHelpButton_clicked(self):
        """Open additional parameters help dialog when button is clicked"""
        try:
            backend_module = getattr(backends, self.backendComboBox.currentText())
        except AttributeError:
            backend_module = backends.soapy_power

        self.params_help_dialog = QSpectrumAnalyzerSettingsHelp(
            backend_module.Info.help_params(self.executableEdit.text()),
            parent=self
        )

        self.params_help_dialog.show()
        self.params_help_dialog.raise_()
        self.params_help_dialog.activateWindow()

    @QtCore.Slot()
    def on_deviceHelpButton_clicked(self):
        """Open device help dialog when button is clicked"""
        try:
            backend_module = getattr(backends, self.backendComboBox.currentText())
        except AttributeError:
            backend_module = backends.soapy_power

        self.device_help_dialog = QSpectrumAnalyzerSettingsHelp(
            backend_module.Info.help_device(self.executableEdit.text(), self.deviceEdit.text()),
            parent=self
        )

        self.device_help_dialog.show()
        self.device_help_dialog.raise_()
        self.device_help_dialog.activateWindow()

    @QtCore.Slot(str)
    def on_backendComboBox_currentIndexChanged(self, text):
        """Load the independent profile when backend is changed."""
        self.load_backend_profile(text)

    def accept(self):
        """Save settings when dialog is accepted"""
        settings = QtCore.QSettings()
        backend = self.backendComboBox.currentText()
        prefix = "backend_profiles/{}/".format(backend)
        settings.setValue("backend", backend)
        settings.setValue("settings_backend", backend)
        settings.setValue("executable", self.executableEdit.text())
        settings.setValue("params", self.paramsEdit.text())
        settings.setValue("device", self.deviceEdit.text())
        settings.setValue("sample_rate", self.sampleRateSpinBox.value() * 1e6)
        settings.setValue("bandwidth", self.bandwidthSpinBox.value() * 1e6)
        settings.setValue(prefix + "executable", self.executableEdit.text())
        settings.setValue(prefix + "params", self.paramsEdit.text())
        settings.setValue(prefix + "device", self.deviceEdit.text())
        settings.setValue(prefix + "sample_rate", self.sampleRateSpinBox.value() * 1e6)
        settings.setValue(prefix + "bandwidth", self.bandwidthSpinBox.value() * 1e6)
        settings.setValue("lnb_lo", self.lnbSpinBox.value() * 1e6)
        settings.setValue("waterfall_history_size", self.waterfallHistorySizeSpinBox.value())
        QtWidgets.QDialog.accept(self)


class QSpectrumAnalyzerSettingsHelp(QtWidgets.QDialog, Ui_QSpectrumAnalyzerSettingsHelp):
    """QSpectrumAnalyzer settings help dialog"""
    def __init__(self, text, parent=None):
        # Initialize UI
        super().__init__(parent)
        self.setupUi(self)

        monospace_font = QtGui.QFont('monospace')
        monospace_font.setStyleHint(QtGui.QFont.Monospace)
        self.helpTextEdit.setFont(monospace_font)
        self.helpTextEdit.setPlainText(text)
