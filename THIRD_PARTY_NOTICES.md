# Сторонние компоненты сборки 2.3

EXE включает неизменённые сторонние библиотеки. Указанные ниже лицензии относятся к соответствующим компонентам, а не автоматически ко всем исходникам приложения.

- **FFmpeg / FFprobe 8.0.1 full build (Gyan)** — GPLv3. Копия лицензии: `LICENSES/GPL-3.0.txt`. Полная информация о сборке и внешних библиотеках: `LICENSES/FFmpeg-8.0.1-build-info.txt`. Точный исходный коммит FFmpeg: https://github.com/FFmpeg/FFmpeg/tree/894da5ca7d ; архив исходников: https://github.com/FFmpeg/FFmpeg/archive/894da5ca7d.tar.gz . Поставщик сборки: https://www.gyan.dev/ffmpeg/builds/ . FFmpeg запускается отдельным процессом.
- **PySide6 Essentials / Shiboken / Qt 6.12.0** — используемые открытые компоненты доступны по LGPLv3; условия альтернативных лицензий указаны в исходных проектах. Тексты LGPLv3 и GPLv3: `LICENSES/LGPL-3.0.txt`, `LICENSES/GPL-3.0.txt`. Исходники Qt: https://github.com/qt/qtbase/tree/v6.12.0 ; PySide/Shiboken: https://github.com/qtproject/pyside-pyside-setup/tree/v6.12.0 . Сведения о лицензиях компонентов Qt: https://doc.qt.io/qtforpython-6/licenses.html . Разрешается замена LGPL-библиотек и обратная разработка для отладки таких изменений. Полный код приложения и инструкции сборки позволяют пересобрать EXE с совместимой изменённой версией библиотек.
- **CPython 3.11.9** — PSF License и лицензии включённых компонентов; полный текст: `LICENSES/Python-3.11.9-LICENSE.txt`. Исходники: https://github.com/python/cpython/tree/v3.11.9 .
- **PyInstaller 6.22.3 bootloader** — GPL с исключением для распространения собранных приложений. Полный текст с исключением: `LICENSES/PyInstaller-COPYING.txt`. Исходники: https://github.com/pyinstaller/pyinstaller/tree/v6.22.3 .
- **Microsoft Visual C++ Runtime** — распространяемые DLL среды выполнения; сведения Microsoft: https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files .

Сохраняйте уведомления и тексты лицензий при дальнейшем распространении. Для запуска приложения отдельная папка с лицензиями не требуется; тексты сопровождают релиз в архиве исходников и доступны в репозитории.
