# Создание DEB-пакета QSpectrumAnalyzer для Debian 13

Руководство описывает [сборщик](../packaging/debian/build.py) и
[проверку установленного пакета](../packaging/debian/check_installed.py).
Целевая система — **Debian 13 «trixie», системный Python 3.13**.
Команды выполняются в терминале Linux.

Сборку можно выполнять на Debian или Ubuntu. Проверка первой сборки
в чистом Debian 13 amd64 прошла на Python 3.13.5, PyQtGraph 0.13.7
и NumPy 2.2.4: 79 тестов успешно. Это результат конкретной сборки;
после изменений проверку нужно повторять. Реальный SDR в контейнере
не использовался.

## 1. Результат сборки

Сборщик создаёт в каталоге проекта **dist/**:

- qspectrumanalyzer_ВЕРСИЯ_all.deb — установочный пакет;
- qspectrumanalyzer_ВЕРСИЯ_all.deb.sha256 — контрольную сумму.

Например:

~~~text
dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb
dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb.sha256
~~~

Архив исходников и журнал тестов не создаются автоматически: их подготовка
описана ниже. Пакет содержит приложение со всеми текущими доработками,
ярлык, SVG-иконку и приватные Python-зависимости:

| Компонент | Версия |
|---|---|
| Qt.py | 2.0.5 |
| soapy_power | 1.6.1 |
| simplesoapy | 1.5.1 |
| simplespectral | 1.0.0 |

Qt, NumPy, SciPy и SoapySDR устанавливаются из системных пакетов.
Поэтому маленький DEB не означает маленький объём установки:
зависимости требуют дополнительного места и загрузки из интернета.

Личные настройки, серверный токен, CSV, слепки, виртуальное окружение
рабочего ПК и мобильный APK в пакет не включаются.

## 2. Подготовка исходников

Нужна актуальная копия нашего доработанного проекта. На рабочем ПК:

~~~bash
cd /home/dock/venv/qspectr/qspectrumanalyzer
git status --short
git log -1 --oneline
~~~

На другом компьютере замените путь на свой. Сборщик копирует **рабочие
файлы**, а не только последний коммит: незакоммиченные изменения и новые
файлы внутри qspectrumanalyzer/ тоже попадут в пакет. Проверьте, что внутри
этого каталога нет посторонних данных. Исключаются только __pycache__,
файлы .pyc и .pyo.

Для сборки нужны:

~~~text
qspectrumanalyzer/
packaging/debian/build.py
packaging/debian/check_installed.py
tests/
LICENSE
qspectrumanalyzer.desktop
qspectrumanalyzer.svg
~~~

Каталог tests/ нужен для проверки. Копия исходного публичного проекта
не заменяет нашу версию, если доработки ещё не опубликованы в нём.

## 3. Инструменты сборки

Установите инструменты на Debian/Ubuntu:

~~~bash
sudo apt update
sudo apt install python3 python3-venv python3-pip dpkg
~~~

Создайте отдельное окружение сборщика:

~~~bash
python3 -m venv "$HOME/venvs/qspectr-deb-build"
source "$HOME/venvs/qspectr-deb-build/bin/activate"
python -m pip install --upgrade pip setuptools wheel
~~~

Это окружение не включается в DEB. Устанавливать в него Qt, драйверы
и само приложение не требуется. Проверьте инструменты:

~~~bash
python --version
python -m pip --version
dpkg-deb --version
~~~

Сборка выполняется **без sudo**. Владельца файлов root:root выставляет
dpkg-deb с параметром --root-owner-group.

## 4. Сборка и нумерация версий

Из корня проекта:

~~~bash
python packaging/debian/build.py --version 2.2.0+custom20260913-1
~~~

Без параметра --version используется значение, записанное в сборщике.
Оно не обновляется автоматически по дате или Git-коммиту.

Для следующей редакции:

~~~bash
python packaging/debian/build.py --version 2.2.0+custom20260913-2
~~~

Состав версии:

- 2.2.0 — базовая версия приложения;
- +custom20260913 — обозначение нашей сборки;
- -1 или -2 — редакция Debian-пакета.

Для распространяемого обновления повышайте версию. Проверка порядка:

~~~bash
dpkg --compare-versions 2.2.0+custom20260913-2 gt 2.2.0+custom20260913-1
echo $?
~~~

Код 0 означает, что новая версия больше старой. Версия DEB отличается
от версии в qspectrumanalyzer/version.py: параметр сборщика не меняет
вывод команды qspectrumanalyzer --version.

### Этапы работы сборщика

1. Проверка допустимости версии Debian.
2. Подготовка четырёх закреплённых зависимостей через pip wheel --no-deps
   в build/deb-wheels/.
3. Создание временного дерева файлов пакета.
4. Копирование приложения и распаковка pure-Python wheels.
5. Замена бинарного чтения numpy.fromstring на numpy.frombuffer
   в приватной копии soapypower/writer.py для совместимости с NumPy 2.
6. Создание запускающих файлов, метаданных, лицензий и контрольных сумм.
7. Сборка DEB и удаление временного дерева.

Сборщик не устанавливает зависимости в системный Python.
Установка готового DEB также не запускает pip.

### Повторная сборка без сети

Сначала подготовьте wheels с доступом к интернету. Когда в build/deb-wheels/
есть все четыре нужные версии, можно выполнить:

~~~bash
PIP_NO_INDEX=1 PIP_FIND_LINKS="$PWD/build/deb-wheels"   python packaging/debian/build.py --version 2.2.0+custom20260913-2
~~~

Окружение сборщика уже должно быть подготовлено. Для новых версий
зависимостей сначала подготовьте соответствующие wheels.

## 5. Состав установленного пакета

| Путь | Назначение |
|---|---|
| /usr/bin/qspectrumanalyzer | Команда запуска |
| /usr/lib/qspectrumanalyzer/qspectrumanalyzer/ | Код приложения |
| /usr/lib/qspectrumanalyzer/vendor/ | Приватные зависимости |
| /usr/lib/qspectrumanalyzer/bin/soapy_power | Встроенный бэкенд |
| /usr/share/applications/qspectrumanalyzer.desktop | Ярлык меню |
| /usr/share/icons/hicolor/scalable/apps/qspectrumanalyzer.svg | Иконка |
| /usr/share/doc/qspectrumanalyzer/ | Лицензии и build.json |

Запускающие файлы используют /usr/bin/python3 -I, добавляют приватные
модули и выбирают PyQt5 для Qt.py и PyQtGraph. Из окружения удаляются
PYTHONPATH, QT_PLUGIN_PATH и QT_QPA_PLATFORM_PLUGIN_PATH, чтобы не
подхватить чужие плагины. Приватный каталог бэкенда добавляется в начало
PATH процесса приложения.

Глобальная команда /usr/bin/soapy_power не устанавливается.
Отдельная проверка бэкенда:

~~~bash
/usr/lib/qspectrumanalyzer/bin/soapy_power --help
~~~

### Системные зависимости

Обязательные Depends:

- python3 версии от 3.13 включительно до 3.14 исключительно;
- python3-numpy, python3-scipy;
- python3-pyqt5, python3-pyqt5.qtsvg, python3-pyqtgraph;
- python3-soapysdr.

Рекомендуемые Recommends:

- soapysdr-tools;
- soapysdr0.8-module-hackrf, soapysdr0.8-module-rtlsdr;
- hackrf, rtl-sdr.

При стандартных настройках APT рекомендуемые пакеты тоже устанавливаются.
Если использовать --no-install-recommends, установите необходимые
SDR-компоненты самостоятельно.

Для других приёмников драйверы и доступ к устройству проверяются отдельно.
Например, модуль PlutoSDR в текущий список зависимостей не включён.

Architecture: all означает отсутствие собственных архитектурных бинарных
модулей в DEB. Это не означает совместимость с любым Linux или Python.
Фактическая проверка выполнена на Debian 13 amd64.

## 6. Проверка содержимого и контрольной суммы

Из корня проекта:

~~~bash
dpkg-deb --info dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb
dpkg-deb --contents dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb
~~~

Проверьте имя, версию, зависимости, ярлык, иконку, запускающие файлы.
Не должно быть venv, пользовательских конфигураций и записей.

SHA-256 проверяется **из каталога с DEB**, поскольку файл контрольной
суммы содержит имя без пути:

~~~bash
(
  cd dist
  sha256sum -c qspectrumanalyzer_2.2.0+custom20260913-1_all.deb.sha256
)
~~~

Ожидаемый результат — OK. Распаковка без установки:

~~~bash
dpkg-deb --extract dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb build/deb-inspect
cat build/deb-inspect/usr/share/doc/qspectrumanalyzer/build.json
~~~

build.json содержит SHA-256 wheels, версию пакета и описание исправления
NumPy. Эти сведения помогают отследить состав сборки, но текущий сборщик
не гарантирует побайтово одинаковый DEB при повторном запуске.

## 7. Проверка в чистом Debian через Docker

Нужен установленный Docker и доступ к его daemon:

~~~bash
docker info
docker pull debian:13-slim
~~~

Из корня проекта выполните:

~~~bash
docker run --rm   -v "$PWD:/src:ro"   debian:13-slim   sh -c 'apt-get update &&
         DEBIAN_FRONTEND=noninteractive apt-get install -y /src/dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb &&
         /usr/bin/python3 -I /src/packaging/debian/check_installed.py'
~~~

При другой версии измените имя DEB. Для сохранения журнала в Bash:

~~~bash
set -o pipefail
docker run --rm   -v "$PWD:/src:ro"   debian:13-slim   sh -c 'apt-get update &&
         DEBIAN_FRONTEND=noninteractive apt-get install -y /src/dist/qspectrumanalyzer_2.2.0+custom20260913-1_all.deb &&
         /usr/bin/python3 -I /src/packaging/debian/check_installed.py'   2>&1 | tee dist/debian13-package-check.log
~~~

Скрипт проверки:

1. Подключает приложение из /usr/lib/qspectrumanalyzer, не из исходников.
2. Выводит версии Python, NumPy и PyQtGraph.
3. Проверяет исправление soapy_power для NumPy 2.
4. Запускает qspectrumanalyzer --version и soapy_power --help.
5. Выполняет тесты из /src/tests с Qt в режиме offscreen.

Ожидаются итог OK и код завершения 0; число тестов может меняться.
Предупреждения Qt «This plugin does not support raise()» в режиме offscreen
сами по себе не означают провал тестов.

Контейнер не получает доступ к USB-приёмнику и графическому сеансу ПК.
Проверка реального сканирования выполняется отдельно на ноутбуке.
После завершения контейнер удаляется; образ Docker остаётся.

## 8. Архив исходников для повторной сборки

Сохраните вместе с DEB исходники, лицензии и подготовленные зависимости.
Из корня проекта:

~~~bash
tar --exclude='__pycache__' --exclude='*.pyc'   -czf dist/qspectrumanalyzer_2.2.0+custom20260913-source.tar.gz   qspectrumanalyzer packaging tests docs   setup.py LICENSE README.rst   qspectrumanalyzer.desktop qspectrumanalyzer.svg qspectrumanalyzer.png   build/deb-wheels
~~~

Проверьте список:

~~~bash
tar -tzf dist/qspectrumanalyzer_2.2.0+custom20260913-source.tar.gz
~~~

Архив не должен содержать venv, .git и личные данные. Сохраняйте лицензии
приложения и зависимостей. Сборщик также помещает их в установленный пакет.

## 9. Установка, обновление и удаление на ноутбуке

Скопируйте DEB и SHA-256 на ноутбук. Из каталога с файлами:

~~~bash
sha256sum -c qspectrumanalyzer_2.2.0+custom20260913-1_all.deb.sha256
sudo apt update
sudo apt install ./qspectrumanalyzer_2.2.0+custom20260913-1_all.deb
~~~

Префикс ./ указывает APT на локальный файл. Используйте apt install,
а не только dpkg -i: APT устанавливает зависимости.

Запускайте QSpectrumAnalyzer из меню приложений либо командой:

~~~bash
qspectrumanalyzer
~~~

Для обновления закройте приложение и установите пакет с более высокой
версией такой же командой. Затем запустите приложение заново:
работающий Python-процесс не подхватывает новый код автоматически.

Настройки находятся в ~/.config/QSpectrumAnalyzer/QSpectrumAnalyzer.conf.
При их переносе исправьте пути к данным и бэкенду. Старый абсолютный путь
из venv другого компьютера может не существовать. Для встроенного бэкенда
используйте soapy_power либо /usr/lib/qspectrumanalyzer/bin/soapy_power.

Проверка обнаружения SDR:

~~~bash
SoapySDRUtil --find
~~~

Затем проверьте сканирование, CSV, создание и анализ слепков. Для телефона
укажите IP ноутбука; сервер включается вручную через File → Mobile server….

Удаление:

~~~bash
sudo apt remove qspectrumanalyzer
~~~

Пакет не удаляет CSV, слепки и настройки из домашнего каталога.

## 10. Изменение сборки и устранение ошибок

| Задача или ошибка | Что проверить |
|---|---|
| Изменить версию DEB | Параметр --version |
| Обновить Python-зависимость | Константа VENDORS в сборщике, затем повторная проверка |
| Добавить системные зависимости | Depends и Recommends в сборщике |
| Изменить иконку | Корневой qspectrumanalyzer.svg |
| Изменить ярлык | Корневой qspectrumanalyzer.desktop |
| No module named pip | Активировать окружение сборщика |
| externally-managed-environment | Использовать venv, не устанавливать pip-пакеты в системный Python |
| dpkg-deb: command not found | Установить пакет dpkg |
| No matching distribution found | Проверить версии, сеть, настройки pip и доступность wheels |
| Unexpected soapy_power writer | Проверить код зависимости и актуальность исправления NumPy |
| too many values to unpack при выборе wheel | Оставить в build/deb-wheels один подходящий wheel данной версии |
| SHA-256: No such file | Запустить проверку из каталога с DEB |
| Несовместимая версия Python | Использовать Debian 13 / Python 3.13; не обходить зависимости принудительно |
| Docker: permission denied | Проверить доступ пользователя к Docker daemon |
| Открывается прежняя копия | Проверить command -v qspectrumanalyzer; пакетная команда находится в /usr/bin |
| Backend executable not found | Исправить сохранённый путь к бэкенду |
| Приёмник не найден | Проверить подключение, драйвер SoapySDR и права USB |

После изменения состава пакета повторите установку и тесты в чистом Debian.
Успешное создание файла DEB само по себе не подтверждает работоспособность
приложения в целевой системе.
