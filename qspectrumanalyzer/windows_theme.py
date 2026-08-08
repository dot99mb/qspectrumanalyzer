"""Windows 11 aware application theming and native window integration."""

from __future__ import annotations

import ctypes
import sys

from qspectrumanalyzer.qt import QtCore, QtGui, QtWidgets


THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)


LIGHT = {
    "window": "#f3f3f3",
    "surface": "#ffffff",
    "surface_alt": "#f9f9f9",
    "border": "#d1d1d1",
    "border_hover": "#9f9f9f",
    "text": "#1b1b1b",
    "muted": "#5d5d5d",
    "accent": "#0067c0",
    "accent_hover": "#1975c5",
    "accent_pressed": "#005a9e",
    "selection": "#cce4f7",
    "disabled": "#a0a0a0",
    "plot": "#101215",
    "plot_text": "#e8e8e8",
}

DARK = {
    "window": "#202020",
    "surface": "#2b2b2b",
    "surface_alt": "#252525",
    "border": "#454545",
    "border_hover": "#666666",
    "text": "#ffffff",
    "muted": "#c5c5c5",
    "accent": "#60cdff",
    "accent_hover": "#7ad5ff",
    "accent_pressed": "#4cc2ff",
    "selection": "#164f6b",
    "disabled": "#777777",
    "plot": "#101215",
    "plot_text": "#e8e8e8",
}


def system_uses_dark_theme() -> bool:
    """Return the Windows app theme preference, with a safe light fallback."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return not bool(value)
    except (OSError, ValueError):
        return False


def resolve_theme(mode: str) -> tuple[str, bool]:
    """Resolve ``system`` to a concrete theme and dark-mode flag."""
    mode = mode if mode in THEMES else THEME_SYSTEM
    dark = system_uses_dark_theme() if mode == THEME_SYSTEM else mode == THEME_DARK
    return (THEME_DARK if dark else THEME_LIGHT), dark


def _palette(colors: dict[str, str]) -> QtGui.QPalette:
    palette = QtGui.QPalette()
    roles = {
        QtGui.QPalette.Window: colors["window"],
        QtGui.QPalette.WindowText: colors["text"],
        QtGui.QPalette.Base: colors["surface"],
        QtGui.QPalette.AlternateBase: colors["surface_alt"],
        QtGui.QPalette.ToolTipBase: colors["surface"],
        QtGui.QPalette.ToolTipText: colors["text"],
        QtGui.QPalette.Text: colors["text"],
        QtGui.QPalette.Button: colors["surface"],
        QtGui.QPalette.ButtonText: colors["text"],
        QtGui.QPalette.Highlight: colors["accent"],
        QtGui.QPalette.HighlightedText: "#ffffff" if colors is LIGHT else "#000000",
        QtGui.QPalette.PlaceholderText: colors["muted"],
    }
    for role, color in roles.items():
        palette.setColor(role, QtGui.QColor(color))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(colors["disabled"]))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(colors["disabled"]))
    return palette


def _stylesheet(colors: dict[str, str]) -> str:
    return """
    * {
        font-family: "Segoe UI Variable", "Segoe UI";
        font-size: 10pt;
    }
    QMainWindow, QDialog { background: %(window)s; color: %(text)s; }
    QMenuBar { background: %(window)s; border-bottom: 1px solid %(border)s; padding: 3px 6px; }
    QMenuBar::item { padding: 6px 10px; border-radius: 4px; }
    QMenuBar::item:selected, QMenuBar::item:pressed { background: %(selection)s; }
    QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px; }
    QMenu::item { padding: 7px 28px 7px 10px; border-radius: 4px; }
    QMenu::item:selected { background: %(selection)s; }
    QToolBar#commandBar {
        background: %(surface_alt)s;
        border: none;
        border-bottom: 1px solid %(border)s;
        padding: 6px 10px;
        spacing: 5px;
    }
    QToolBar#commandBar QToolButton { padding: 6px 10px; }
    QToolBar::separator { background: %(border)s; width: 1px; margin: 5px 7px; }
    QDockWidget { color: %(text)s; font-weight: 600; titlebar-close-icon: none; }
    QDockWidget::title { background: %(surface_alt)s; padding: 8px 10px; border-bottom: 1px solid %(border)s; }
    QDockWidget > QWidget { background: %(surface)s; }
    QPushButton, QToolButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
        min-height: 28px;
        color: %(text)s;
        background: %(surface)s;
        border: 1px solid %(border)s;
        border-radius: 5px;
        padding: 2px 8px;
    }
    QPushButton:hover, QToolButton:hover, QComboBox:hover, QSpinBox:hover,
    QDoubleSpinBox:hover, QLineEdit:hover { border-color: %(border_hover)s; background: %(surface_alt)s; }
    QPushButton:pressed, QToolButton:pressed { background: %(selection)s; }
    QPushButton:disabled, QToolButton:disabled { color: %(disabled)s; }
    QPushButton#startButton { color: white; background: %(accent)s; border-color: %(accent)s; font-weight: 600; }
    QPushButton#startButton:hover { background: %(accent_hover)s; }
    QPushButton#startButton:pressed { background: %(accent_pressed)s; }
    QComboBox::drop-down { border: none; width: 24px; }
    QCheckBox { spacing: 7px; min-height: 24px; }
    QTabBar::tab { background: %(surface_alt)s; color: %(text)s; padding: 7px 14px; border: 1px solid %(border)s; }
    QTabBar::tab:selected { background: %(surface)s; border-bottom-color: %(accent)s; }
    QTableView { background: %(surface)s; alternate-background-color: %(surface_alt)s; color: %(text)s; border: 1px solid %(border)s; gridline-color: %(border)s; selection-background-color: %(selection)s; }
    QHeaderView::section { background: %(surface_alt)s; color: %(text)s; border: none; border-bottom: 1px solid %(border)s; padding: 6px; }
    QStatusBar { background: %(surface_alt)s; color: %(muted)s; border-top: 1px solid %(border)s; }
    QProgressBar { min-height: 8px; max-height: 8px; border: none; border-radius: 4px; background: %(border)s; text-align: center; }
    QProgressBar::chunk { border-radius: 4px; background: %(accent)s; }
    QSplitter::handle { background: %(border)s; }
    QToolTip { color: %(text)s; background: %(surface)s; border: 1px solid %(border)s; padding: 5px; }
    """ % colors


def apply_application_theme(app: QtWidgets.QApplication, mode: str) -> tuple[str, bool]:
    """Apply the requested application theme and return its resolved state."""
    resolved, dark = resolve_theme(mode)
    colors = DARK if dark else LIGHT
    app.setStyle("Fusion")
    app.setPalette(_palette(colors))
    app.setStyleSheet(_stylesheet(colors))
    return resolved, dark


def apply_native_window_theme(window: QtWidgets.QWidget, dark: bool) -> None:
    """Enable a matching title bar and rounded corners on Windows 11."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if dark else 0)
        # Attribute 20 is DWMWA_USE_IMMERSIVE_DARK_MODE on supported builds.
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
        )
        if result != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 19, ctypes.byref(value), ctypes.sizeof(value)
            )
        # DWMWA_WINDOW_CORNER_PREFERENCE / DWMWCP_ROUND.
        corner = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner)
        )
    except (AttributeError, OSError, ValueError):
        pass


def configure_high_dpi() -> None:
    """Request high-DPI pixmaps where the active Qt version exposes the flag."""
    attribute = getattr(QtCore.Qt, "AA_UseHighDpiPixmaps", None)
    if attribute is not None:
        QtCore.QCoreApplication.setAttribute(attribute, True)
