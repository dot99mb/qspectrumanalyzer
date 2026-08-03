# Установка QSpectrumAnalyzer в Ubuntu 26.04

Эта инструкция описывает установку текущей версии QSpectrumAnalyzer из
репозитория GitHub и настройку приложения для HackRF, RTL-SDR и PlutoSDR.

> Не переносите готовое виртуальное окружение Python (`venv`) с другого
> компьютера. Оно содержит абсолютные пути, ссылки на системный Python и
> бинарные модули. На новом компьютере виртуальное окружение нужно создать
> заново.

## 1. Проверка Ubuntu и Python

Откройте терминал и выполните:

```bash
source /etc/os-release
echo "$PRETTY_NAME"
python3 --version
```

Инструкция рассчитана на Ubuntu Desktop 26.04 и системный Python 3.14.

## 2. Установка системных компонентов

Подключите репозиторий Universe и установите зависимости:

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository universe
sudo apt update

sudo apt install -y \
    git \
    build-essential \
    python3 \
    python3-dev \
    python3-venv \
    python3-pip \
    python3-setuptools \
    python3-pyqt5 \
    python3-pyqtgraph \
    python3-numpy \
    python3-scipy \
    python3-soapysdr \
    soapysdr-tools \
    hackrf \
    rtl-sdr \
    libiio-utils \
    soapysdr0.8-module-hackrf \
    soapysdr0.8-module-rtlsdr \
    soapysdr0.8-module-plutosdr
```

Будут установлены:

- PyQt5 и PyQtGraph для интерфейса;
- NumPy и SciPy для обработки спектра;
- SoapySDR и его Python bindings;
- `hackrf_sweep`;
- `rtl_test` и `rtl_power`;
- libiio для PlutoSDR;
- плагины SoapySDR для HackRF, RTL-SDR и PlutoSDR.

SciPy желательно оставить установленным. Без SciPy или pyFFTW пакет
`soapy_power` использует более медленный вариант FFT из NumPy.

## 3. Настройка доступа к USB

Добавьте текущего пользователя в группу `plugdev`, перечитайте правила udev:

```bash
sudo usermod -aG plugdev "$USER"
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Затем:

1. отключите SDR-устройства;
2. выйдите из учётной записи Ubuntu;
3. войдите снова;
4. подключите устройства.

Проверьте членство в группе:

```bash
groups
```

В выводе должна присутствовать группа `plugdev`.

Пакеты Ubuntu устанавливают правила udev для HackRF, RTL-SDR и libiio.
Дополнительная информация по PlutoSDR:
[Linux Drivers — Analog Devices](https://wiki.analog.com/university/tools/pluto/drivers/linux).

## 4. Получение исходного кода

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"

git clone https://github.com/dot99mb/qspectrumanalyzer.git
cd qspectrumanalyzer
```

Проверьте ветку, состояние рабочего дерева и полученный коммит:

```bash
git status
git log -1 --oneline
git rev-parse HEAD
```

Команда `git clone` получает актуальную версию ветки `master`.

## 5. Создание виртуального окружения

Системный Python binding SoapySDR должен быть виден из виртуального окружения.
Поэтому параметр `--system-site-packages` обязателен:

```bash
mkdir -p "$HOME/venvs"

python3 -m venv \
    --system-site-packages \
    "$HOME/venvs/qspectrumanalyzer"

source "$HOME/venvs/qspectrumanalyzer/bin/activate"
```

Проверьте активный Python:

```bash
which python
python --version
```

Путь должен выглядеть так:

```text
/home/ИМЯ_ПОЛЬЗОВАТЕЛЯ/venvs/qspectrumanalyzer/bin/python
```

Если создать обычный изолированный `venv`, приложение не увидит системный
SoapySDR и завершится с ошибкой:

```text
ModuleNotFoundError: No module named 'SoapySDR'
```

## 6. Установка приложения из исходного кода

```bash
python -m pip install --upgrade pip setuptools wheel

cd "$HOME/src/qspectrumanalyzer"
python -m pip install --editable .
```

Режим `--editable` удобен для работы с исходным кодом: приложение использует
файлы непосредственно из репозитория, поэтому после обновления Git не нужно
копировать проект.

Проверьте зависимости:

```bash
python -m pip check
```

Проверьте основные импорты:

```bash
python - <<'PY'
import PyQt5
import pyqtgraph
import numpy
import scipy
import SoapySDR
import soapypower
import qspectrumanalyzer

print("PyQt5: OK")
print("PyQtGraph:", pyqtgraph.__version__)
print("NumPy:", numpy.__version__)
print("SciPy:", scipy.__version__)
print("SoapySDR:", SoapySDR.getAPIVersion())
print("QSpectrumAnalyzer:", qspectrumanalyzer.__file__)
PY
```

## 7. Совместимость soapy_power с NumPy 2.x

Версия `soapy_power 1.6.1` использует удалённый в NumPy 2.x бинарный режим
`numpy.fromstring()`. Без исправления в терминале многократно появляется:

```text
The binary mode of fromstring is removed, use frombuffer instead
```

Найдите установленный файл и замените вызов:

```bash
SOAPY_WRITER="$(
    python -c 'import soapypower.writer; print(soapypower.writer.__file__)'
)"

sed -i \
    's/numpy\.fromstring(/numpy.frombuffer(/' \
    "$SOAPY_WRITER"

grep -n 'frombuffer' "$SOAPY_WRITER"
```

В файле должна быть строка:

```python
pwr_array = numpy.frombuffer(f.read(header.size), dtype='float32')
```

Проверка бинарного формата:

```bash
python - <<'PY'
import io
import numpy as np
from soapypower.writer import SoapyPowerBinFormat

formatter = SoapyPowerBinFormat()
source = np.array([-100.0, -80.5, -65.0], dtype=np.float32)

buffer = io.BytesIO()
formatter.write(
    buffer,
    1.0,
    2.0,
    100e6,
    103e6,
    1e6,
    1024,
    source,
)

buffer.seek(0)
header, restored = formatter.read(buffer)
np.testing.assert_array_equal(restored, source)
print("soapy_power binary format: OK")
PY
```

## 8. Проверка SoapySDR

Проверьте установленные модули:

```bash
SoapySDRUtil --info
```

Подключите одно SDR-устройство и выполните:

```bash
SoapySDRUtil --find
soapy_power --detect
```

Проверка через Python:

```bash
python - <<'PY'
import SoapySDR

devices = SoapySDR.Device.enumerate()
if not devices:
    print("SoapySDR не обнаружил устройства")
else:
    for number, device in enumerate(devices, 1):
        print(number, dict(device))
PY
```

Одно устройство не может одновременно использоваться несколькими
приложениями. Перед проверкой закройте GQRX, SDRangel, GNU Radio, CubicSDR и
другие SDR-программы.

## 9. Проверка HackRF

```bash
hackrf_info
SoapySDRUtil --find="driver=hackrf"
```

Однократный тест `hackrf_sweep`:

```bash
hackrf_sweep \
    -f 100:110 \
    -w 100000 \
    -1 \
    > /tmp/hackrf-sweep.csv

head /tmp/hackrf-sweep.csv
```

В приложении HackRF может работать через два backend:

- `hackrf_sweep` — быстрый широкополосный обзор;
- `soapy_power` — универсальный FFT-режим через SoapySDR.

## 10. Проверка RTL-SDR

```bash
rtl_test -t
SoapySDRUtil --find="driver=rtlsdr"
```

Если `rtl_test` сообщает, что устройство занято kernel driver, DVB-модуль
использует приёмник как телевизионный тюнер. Его можно отключить:

```bash
printf '%s\n' \
    'blacklist dvb_usb_rtl28xxu' \
    'blacklist rtl2832' \
    'blacklist rtl2830' |
sudo tee /etc/modprobe.d/blacklist-rtl-sdr.conf

sudo reboot
```

После этого RTL-SDR нельзя будет использовать как DVB-T-тюнер, но он станет
доступен `rtl-sdr` и SoapySDR.

Для RTL-SDR рекомендуется backend `soapy_power`. Backend `rtl_power` также
доступен, но имеет ограничения при непрерывном обновлении и cropping.

## 11. Проверка PlutoSDR

Подключите PlutoSDR к разъёму USB OTG/Data, а не только к разъёму питания.

```bash
iio_info -s
ping -c 3 pluto.local
SoapySDRUtil --find="driver=plutosdr"
```

`iio_info -s` должен показать контекст вида `usb:...` или `ip:pluto.local`.
Если обнаружение по USB работает, доступность имени `pluto.local` не
обязательна.

Дополнительная информация:

- [PlutoSDR Linux drivers](https://wiki.analog.com/university/tools/pluto/drivers/linux);
- [libiio и URI устройств](https://wiki.analog.com/university/tools/pluto/controlling_the_transceiver_and_transferring_data);
- [SoapyPlutoSDR](https://github.com/pothosware/SoapyPlutoSDR).

Для PlutoSDR используется backend `soapy_power`.

## 12. Запуск

С активированным окружением:

```bash
source "$HOME/venvs/qspectrumanalyzer/bin/activate"
cd "$HOME/src/qspectrumanalyzer"
qspectrumanalyzer
```

Без предварительной активации:

```bash
"$HOME/venvs/qspectrumanalyzer/bin/qspectrumanalyzer"
```

Предупреждение Qt:

```text
Warning: Ignoring XDG_SESSION_TYPE=wayland on Gnome
```

не является критической ошибкой.

## 13. Настройка backend в приложении

Backend выбирается через **File → Settings**. После его смены приложение может
сбросить часть основных параметров на значения нового backend. Поэтому сначала
выберите backend и нажмите **OK**, затем задавайте диапазон, размер bin,
интервал и gain.

### HackRF через hackrf_sweep

Настройки backend:

```text
Backend:    hackrf_sweep
Executable: hackrf_sweep
Device:     пусто
```

Безопасные начальные параметры:

```text
Start:      100 MHz
Stop:       200 MHz
Bin size:   40–100 kHz
Interval:   0.1–0.25 s
Gain:       20–40 dB
```

`Interval = 0` отображает каждый проход, но заметно увеличивает нагрузку CPU.

### HackRF через SoapySDR

```text
Backend:     soapy_power
Executable:  soapy_power
Device:      driver=hackrf
Sample rate: 20 MHz
Bandwidth:   0 или 20 MHz
Bin size:    20–100 kHz
Interval:    0.25–1 s
Gain:        20–40 dB
```

### RTL-SDR через SoapySDR

```text
Backend:     soapy_power
Executable:  soapy_power
Device:      driver=rtlsdr
Sample rate: 2.4 MHz
Bandwidth:   0
Bin size:    10–50 kHz
Interval:    0.5–1 s
Gain:        20–35 dB
Crop:        10–20%
```

### PlutoSDR через SoapySDR

```text
Backend:     soapy_power
Executable:  soapy_power
Device:      driver=plutosdr
Sample rate: 2.5 MHz
Bandwidth:   2 MHz
Bin size:    10–50 kHz
Interval:    0.5–1 s
Gain:        20–50 dB
```

Если подключено несколько устройств или краткий selector не работает,
скопируйте точные параметры устройства из:

```bash
SoapySDRUtil --find
```

Selector PlutoSDR может дополнительно содержать URI `usb:...` или
`ip:pluto.local`.

SoapyPlutoSDR не реализует обычную API-коррекцию PPM. При необходимости
корректируйте `xo_correction` в файле `config.txt` PlutoSDR.

## 14. Добавление приложения в меню Ubuntu

```bash
mkdir -p "$HOME/.local/share/applications"

sed \
    -e "s|^Exec=.*|Exec=$HOME/venvs/qspectrumanalyzer/bin/qspectrumanalyzer|" \
    -e "s|^Icon=.*|Icon=$HOME/src/qspectrumanalyzer/qspectrumanalyzer.png|" \
    "$HOME/src/qspectrumanalyzer/qspectrumanalyzer.desktop" \
    > "$HOME/.local/share/applications/qspectrumanalyzer.desktop"

update-desktop-database \
    "$HOME/.local/share/applications" \
    2>/dev/null || true
```

После повторного входа приложение должно появиться в меню Ubuntu.

## 15. Обновление приложения

Закройте QSpectrumAnalyzer:

```bash
cd "$HOME/src/qspectrumanalyzer"
git status
git pull --ff-only

source "$HOME/venvs/qspectrumanalyzer/bin/activate"
python -m pip install --editable .
```

После обновления Python-зависимостей проверьте исправление `soapy_power`:

```bash
SOAPY_WRITER="$(
    python -c 'import soapypower.writer; print(soapypower.writer.__file__)'
)"

grep -n 'frombuffer' "$SOAPY_WRITER"
```

Если снова появился `numpy.fromstring`, повторите замену из раздела 7.

## 16. Частые ошибки

### `No module named SoapySDR`

Виртуальное окружение создано без `--system-site-packages`. Пересоздайте его
по разделу 5.

### `The binary mode of fromstring is removed`

Не применено исправление `soapy_power` из раздела 7.

### `No devices found`

Проверьте:

```bash
groups
lsusb
SoapySDRUtil --info
SoapySDRUtil --find
```

После изменения групп нужно полностью выйти из учётной записи и войти снова.

### `Device or resource busy`

Устройство открыто другим SDR-приложением или предыдущим backend. Остановите
измерение и закройте другие программы.

### RTL-SDR определяется, но не открывается

Вероятно, устройство занял kernel-модуль `dvb_usb_rtl28xxu`. Выполните
настройку из раздела 10.

### PlutoSDR не определяется

Проверьте разъём Data/OTG, USB-кабель, вывод `iio_info -s` и доступность
`pluto.local`.

### Интерфейс обновляется слишком медленно

Уменьшите `Interval`. Для `hackrf_sweep` разумная отправная точка —
`0.1–0.25 s`. Значение `0` даёт максимальную частоту обновления, но сильно
увеличивает нагрузку процессора.
