# Сборка и выпуск Guild Tracker

## Состав

В Git хранятся исходники, шаблоны бейджей, иконка, тесты, документация и сценарии сборки. EXE, ZIP, среды Python, базы, ключи, настройки, захваты, локальные аудиты и референсы в коммит не входят.

Релизные файлы создаются в `release/<версия>/`:

- `GuildTracker.exe` — portable-приложение; стабильное имя для скачивания и обновления.
- `GuildTracker-<версия>-windows-x64.zip` — EXE, инструкция пользователя и изменения версии.
- `SHA256SUMS.txt` — контрольные суммы файлов выпуска.
- `build-info.json` — версия Python, исходный коммит, признак незакоммиченных изменений и результат проверки EXE.
- `release-notes.md` — описание текущего выпуска из CHANGELOG.

Архив собирается по явному списку файлов. Содержимое рабочего `dist/` целиком в него не копируется.

## Локальная подготовка

Нужны Windows x64 и Python 3.12. Сборка использует отдельное окружение `.venv-release/` и версии из `requirements-release.txt`.

На машине сборки нужен актуальный [Microsoft Visual C++ Redistributable x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist). Сценарий берёт единый комплект DLL из System32: старые копии из Java/PATH и Qt не должны подменять библиотеки ONNX Runtime. Эти DLL включаются в EXE.

```powershell
# Если окружение GF2TTK уже создано через setup.bat:
.\release.ps1

# Либо передайте путь к Python 3.12 x64:
.\release.ps1 -PythonExe 'C:\path\to\Python312\python.exe'
```

Порядок: установка закреплённых зависимостей → `pip check` → проверка списка файлов Git → тесты → PyInstaller → запуск копии EXE в пустой временной папке → ZIP и SHA-256. Проверка EXE не требует игры, Npcap или Google и не открывает пользовательскую базу. Она проверяет Qt, SQLite, две OCR-модели, шаблоны и версию.

Перед запуском на незакоммиченном проекте подготовьте индекс (`git add` только проверенных файлов), поскольку `scripts/check_release.py` проверяет именно содержимое будущего коммита. Сборка допускает незакоммиченные изменения и отмечает их в `build-info.json`. Для публикуемого выпуска предпочтительна сборка из отправленного тега: тогда происхождение артефакта однозначно связано с коммитом.

Версия задаётся в `VERSION` и включается в свойства Windows EXE и заголовок приложения. Меняйте также раздел `## [<версия>]` в `CHANGELOG.md` и актуальный номер в README. Один выпущенный номер не используйте для разных сборок.

Для обычной разработки без упаковки выпуска остаётся `build.ps1`; он создаёт `dist/GuildTracker.exe`. Эта команда не выполняет весь набор релизных проверок.

## GitHub Release

После проверки и **отдельного решения о коммите и публикации**:

1. Сделайте коммит подготовленных файлов и отправьте его в GitHub.
2. Создайте и отправьте тег, совпадающий с VERSION:

   ```powershell
   git tag -a v1.0.0 -m 'Guild Tracker 1.0.0'
   git push origin v1.0.0
   ```

3. Workflow **Windows release** соберёт проект на Windows, загрузит артефакты и создаст **черновик** релиза с EXE, ZIP и суммами. Проверьте его и опубликуйте вручную.

Через **Actions → Windows release → Run workflow** можно проверить сборку ветки без релиза: файлы будут доступны как Actions artifact. При запуске на теге workflow также создаёт черновик. Если релиз этого номера уже существует, замена его файлов блокируется.

Для нового выпуска достаточно обновить VERSION/CHANGELOG/README, проверить изменения, закоммитить их и отправить новый тег. Постоянная ссылка на EXE последнего опубликованного стабильного выпуска:

`https://github.com/Agress0r/GF2GuildTK/releases/latest/download/GuildTracker.exe`

Локальный готовый комплект можно также прикрепить к черновику через интерфейс Releases. Автоматизация использует [GitHub CLI release create](https://cli.github.com/manual/gh_release_create), режим `--draft` и `--verify-tag`. Возможность ручного запуска описана в [документации GitHub Actions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

## Проверки перед публикацией

```powershell
.\GF2TTK\Scripts\python.exe scripts\check_release.py
git diff --cached --check
git diff --cached --stat
Get-FileHash release\1.0.0\GuildTracker.exe -Algorithm SHA256
```

`check_release.py` обнаруживает пользовательские данные, приватные ключи, локальные служебные файлы и крупные артефакты в Git index. Эта проверка дополняет просмотр diff. EXE не помещается в историю Git: он распространяется через [вложения Releases](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
