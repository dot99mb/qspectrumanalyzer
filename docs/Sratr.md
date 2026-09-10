## Запуск через ретминал с активацией окружения

```
source /home/dock/venv/qspectr/bin/activate
cd /home/dock/venv/qspectr/qspectrumanalyzer
QT_PREFERRED_BINDING=PyQt5 PYQTGRAPH_QT_LIB=PyQt5 qspectrumanalyzer
```

## Ярлык

путь
```
/home/dock/.local/share/applications
```

текст
```
[Desktop Entry]
Version=1.0
Type=Application
Name=QSpectrumAnalyzer
Comment=Spectrum Analyzer
Icon=qspectrumanalyzer
Exec=env -u PYTHONPATH QT_PREFERRED_BINDING=PyQt5 PYQTGRAPH_QT_LIB=PyQt5 QT_QPA_PLATFORM=xcb /home/dock/venv/qspectr/bin/qspectrumanalyzer
Categories=Science;Electronics;
StartupNotify=true
Terminal=true
```


