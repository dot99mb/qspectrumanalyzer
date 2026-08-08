"""Qt 6 imports used by the application.

Keeping the binding in one small module makes the rest of the code independent
from binding-specific import paths and gives Windows builds one deterministic Qt
runtime.
"""

from PySide6 import QtCore, QtGui, QtWidgets


QT_BINDING = "PySide6"
