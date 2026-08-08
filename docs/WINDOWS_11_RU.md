# QSpectrumAnalyzer для Windows 11

Версия сохраняет все режимы исходного приложения и использует Qt 6/PySide6,
масштабирование Windows, системную светлую/тёмную тему и современную командную
панель. Поддерживаются `soapy_power`, `hackrf_sweep`, `rtl_power`,
`rtl_power_fftw` и `rx_power`.

## Что требуется

- Windows 11 x64;
- Python 3.12 x64 для запуска из исходников или сборки;
- драйвер конкретного SDR и соответствующая backend-утилита;
- для универсального backend: SoapySDR, модуль устройства и `soapy_power`.

Для USB-устройств RTL-SDR/HackRF обычно нужен WinUSB-драйвер. При работе с
Zadig выбирайте только интерфейс подключённого SDR: замена драйвера другого
USB-устройства может нарушить его работу.

## Запуск из исходников

Откройте PowerShell в каталоге проекта и выполните:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\run-windows.ps1
```

Скрипт создаёт локальное окружение `.venv`, устанавливает зависимости и
запускает приложение. В `File → Settings` выберите backend, устройство и путь
к `.exe`. Кнопка `Test backend` проверяет, что команда найдена.

## Сборка приложения

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows.ps1
```

Результат появится в `dist\QSpectrumAnalyzer\QSpectrumAnalyzer.exe`. Это
GUI-сборка приложения; драйвер SDR, SoapySDR и нативные backend-утилиты
устанавливаются отдельно, поскольку их набор зависит от оборудования.

## Первый запуск

1. Подключите SDR и установите его драйвер.
2. Откройте `File → Settings`.
3. Для большинства устройств выберите `soapy_power`; для быстрого широкого
   обзора HackRF можно выбрать `hackrf_sweep`.
4. Нажмите `Test backend`, затем используйте `Device info`, если кнопка
   доступна.
5. Задайте диапазон, bin size, gain и crop, затем нажмите `Start`.

Настройки хранятся в профиле текущего пользователя; раскладка панелей, тема и
параметры измерения восстанавливаются автоматически.

## Настройка RTL-SDR

1. Через Zadig установите WinUSB только для подключённого RTL-SDR. Перед
   заменой драйвера внимательно проверьте имя USB-устройства.
2. Установите комплект RTL-SDR command-line tools. `rtl_power.exe` и DLL из
   того же комплекта можно положить в `tools\rtl-sdr` рядом с
   `QSpectrumAnalyzer.exe`; приложение проверяет эту папку автоматически.
3. Если в комплекте есть `rtl_test.exe`, сначала запустите `rtl_test`,
   убедитесь, что устройство открывается и данные принимаются, затем остановите
   проверку сочетанием Ctrl+C. Параметр `-t` предназначен для отдельного теста
   тюнера E4000 и не подходит для обычного R820T/R828D.
4. В QSpectrumAnalyzer откройте `File → Settings` и нажмите
   `RTL-SDR preset`. Должны появиться:
   - Backend: `rtl_power`;
   - Executable: путь к `rtl_power.exe` либо `rtl_power`;
   - Additional parameters: пусто;
   - Device: `0`;
   - Sample rate: `2.400 MHz`.
5. Нажмите `Test backend`, затем `OK`. Для первого FM-теста используйте
   87–108 MHz, bin size 10 kHz, interval 1 s и crop 20%.

Если `Test backend` сообщает, что команда не найдена, нажмите кнопку `...` и
укажите `rtl_power.exe` вручную. Не выбирайте `soapy_power.exe`, когда Backend
установлен в `rtl_power`.
