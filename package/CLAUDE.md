# Huddle Notes — инструкция для Claude

Эта папка делает заметки и расшифровки Slack-хадлов команды. Ты помогаешь её владельцу:
устанавливаешь всё один раз, отвечаешь на вопросы по звонкам и записываешь пожелания
к заметкам.

```
Huddle Notes/
  Записи/       сюда OBS сохраняет видео хадлов
  Результаты/   один файл на звонок: Заметки + Расшифровка; .huddle-notes-journal.json — что обработано
  данные/       словари, модели, work/, logs/; пожелания.md — пожелания команды к заметкам;
                people/ — база лиц и голосов (биометрия), только если имена включены
  программа/    код huddle-notes; не меняй его, пока об этом не попросили
  whisper/      whisper.cpp для Windows (появится при установке)
  .env          настройки и секреты; никогда не показывай токены в чате
```

Как это работает: программа-наблюдатель запускается при входе в Windows, замечает новую
запись в «Записи», делает расшифровку (Whisper), просит Claude Code (`claude -p`, без
инструментов) написать заметки, кладёт файл в «Результаты» и присылает сообщение в Slack:
название звонка, имя файла записи и файл. Примерно раз в 5 минут он читает ответы в треде под своими
сообщениями: каждый ответ становится пожеланием в `данные\пожелания.md`, а бот отвечает в треде
«Записал». На 👎 без слов бот один раз спрашивает в треде, что не так. Пожелания Claude получает
вместе с каждой новой расшифровкой; уже готовые заметки они не меняют. Ответы, написанные до
первого запуска этой версии, пожеланиями не считаются.

**Имена говорящих выключены** (`HUDDLE_SPEAKERS=off`, так по умолчанию): расшифровка — текст
со временем, без подписей, кто говорит. Claude просят называть человека в заметках, только
если имя прямо прозвучало в разговоре. Из записи берётся только звук, изображение не
разбирается.

## Установка (один раз, Windows, PowerShell)

Все команды — из корня этой папки. Каждая команда, которую ты запускаешь из приложения Claude,
получает PATH, каким он был при запуске приложения, поэтому только что установленную программу
она может не видеть. Тогда обнови PATH в начале команды (между командами он не сохраняется)
или попроси владельца полностью выйти из приложения Claude и открыть его снова:
```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
```

1. Инструменты:
   ```powershell
   winget install --id astral-sh.uv -e
   winget install --id Gyan.FFmpeg -e
   ```
   Проверь: `uv --version`, `ffmpeg -version`, `ffprobe -version`.
2. Whisper. Если есть видеокарта NVIDIA (`nvidia-smi` работает), возьми CUDA-сборку,
   иначе обычную. Обе — из релиза whisper.cpp `b5454` (сборка того же дня, что v1.9.5):
   - обычная: https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip
   - NVIDIA: https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-win-cuda-12.4.0-x64.zip

   Распакуй в `whisper\` и найди `whisper-cli.exe`. Если CUDA-сборка не запускается
   (не хватает библиотек CUDA), поставь обычную. Если он не прямо в `whisper\`, поправь
   путь `HUDDLE_WHISPER_BIN` в `.env`. Проверь: `whisper\whisper-cli.exe --help`.
3. Программа:
   ```powershell
   cd программа
   uv venv --python 3.12 .venv
   uv pip install --python .venv\Scripts\python.exe .
   cd ..
   ```
4. Модели (около 1,7 ГБ, контрольные суммы проверяются):
   ```powershell
   программа\.venv\Scripts\huddle-notes.exe models download
   программа\.venv\Scripts\huddle-notes.exe models
   ```
   У модели whisper должно стоять `downloaded`. Модель голосов скачивается и нужна, только
   если имена включены (`HUDDLE_SPEAKERS=on`).
5. Claude Code для терминала: через команду `claude` программа пишет заметки. Приложение
   Claude эту команду не ставит. По имени твой терминал её может не найти (см. выше про PATH),
   поэтому вызывай её полным путём `& "$env:USERPROFILE\.local\bin\claude.exe"`.
   Проверь, стоит ли она: `& "$env:USERPROFILE\.local\bin\claude.exe" --version` печатает версию.
   Если нет, поставь:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```
   Скачивается около 350 МБ без индикатора: дай команде до 10 минут. При ошибке
   `SSL/TLS secure channel` добавь в начало команды
   `[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;`.
   Установщик кладёт `claude.exe` в `%USERPROFILE%\.local\bin`, но эту папку в PATH сам не
   добавляет (пишет об этом в `Setup notes`). Добавь её в PATH пользователя, чтобы владелец мог
   набирать просто `claude`:
   ```powershell
   $p = [Environment]::GetEnvironmentVariable('PATH', 'User'); [Environment]::SetEnvironmentVariable('PATH', "$p;$env:USERPROFILE\.local\bin", 'User')
   ```
   Программе PATH не нужен: она сама ищет `claude` в этой папке.

   **Ключи API.** Если в Windows или в `.env` задан `ANTHROPIC_API_KEY` или
   `ANTHROPIC_AUTH_TOKEN`, `claude -p` возьмёт его вместо подписки. Приложение Claude убирает
   эти переменные из твоих команд, поэтому смотри прямо в настройки Windows и в `.env`
   (печатается только True или False, не сам ключ):
   ```powershell
   foreach ($n in 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN') { foreach ($t in 'User', 'Machine') { "$n $t " + [bool][Environment]::GetEnvironmentVariable($n, $t) } }
   [bool](Select-String -Path .env -Pattern '^\s*ANTHROPIC_(API_KEY|AUTH_TOKEN)\s*=\s*\S' -Quiet)
   ```
   Везде должно быть False. Если где-то True, скажи владельцу: пусть удалит переменную в
   переменных среды Windows (поиск в Пуске: «переменные среды») или строку в `.env`, потом
   выйдет из Windows и войдёт снова. Сам ничего не удаляй.

   **Вход.** Владелец входит сам, ты за него не войдёшь. Попроси его открыть PowerShell и
   выполнить `& "$env:USERPROFILE\.local\bin\claude.exe" auth login`: откроется браузер, там
   войти аккаунтом с платной подпиской Claude. Дождись, пока он скажет, что вошёл, и проверь:
   ```powershell
   & "$env:USERPROFILE\.local\bin\claude.exe" auth status --text
   & "$env:USERPROFILE\.local\bin\claude.exe" -p "Reply with one word: works" --model claude-opus-5-5 --tools=
   ```
   Пустой список инструментов пиши как `--tools=`: PowerShell 5.1 выбрасывает из команды `""`.
6. Slack: владелец вписывает в `.env` `SLACK_BOT_TOKEN` (сам, не через чат). `SLACK_CHANNEL`
   и `SLACK_NOTIFY_USER` уже заполнены. Бот `@huddle_notes` должен быть в канале.
7. Автозапуск наблюдателя при входе в Windows:
   ```powershell
   $root = (Get-Location).Path
   $exe = Join-Path $root "программа\.venv\Scripts\huddle-notes.exe"
   $log = Join-Path $root "данные\logs\watch.log"
   $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
     -Argument "-NoProfile -WindowStyle Hidden -Command `"& '$exe' --log '$log' watch`""
   $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
   Register-ScheduledTask -TaskName "Huddle Notes" -Action $action -Trigger $trigger `
     -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1))
   Start-ScheduledTask -TaskName "Huddle Notes"
   ```
   Проверь, что в `данные\logs\watch.log` появилась строка `watching ...\Записи`.
8. OBS: Настройки → Вывод → Путь записи → папка `Записи` этой папки. Формат mkv или mp4.
   Источник — «Захват окна» на окно хадла Slack, плюс звук рабочего стола и микрофон.

## Проверка

Положи в «Записи» короткую запись хадла (5–10 минут). Через минуту в логе должно быть
`processing ...`, потом в «Результатах» появится файл, а в Slack — сообщение. В нём не должно
быть «Заметки не получились», а под названием — имя файла этой записи. Покажи владельцу
время обработки из лога и заметки. Подписей, кто говорит, в расшифровке нет — так задумано.

## Что делать по просьбам владельца

- **«Обработай запись» вручную.** Найди запись в «Записи» (по журналу) и запусти:
  ```powershell
  программа\.venv\Scripts\huddle-notes.exe process "Записи\<запись>" --notify
  ```
  Ручная обработка журнал не трогает. Если этой записи в журнале нет или она там не доведена
  до конца (упала меньше трёх раз, не отправлена, без заметок), наблюдатель обработает её сам
  и пришлёт второе сообщение. Чтобы этого не было: сначала `Stop-ScheduledTask -TaskName
  "Huddle Notes"` (работающий наблюдатель перезапишет журнал), потом обработка, потом в
  `Результаты\.huddle-notes-journal.json` у этой записи: `status` "done", `attempts` 0,
  `delivered` true, `notes_error` null, `retry_after` null, `size` — текущий размер файла
  записи; потом `Start-ScheduledTask -TaskName "Huddle Notes"`.
- **Вопросы по звонкам** («что поручили Жене?», «где говорили про iOS?»): ищи в файлах
  «Результатов», отвечай со временем из расшифровки. Помни, что расшифровка бывает неточной и
  в ней не подписано, кто говорит: называй человека, только если это видно из самого текста.
- **«Включи имена»** (кто какую реплику сказал): это владелец решает вместе с разработчиком.
  В `.env` замени значение в строке `HUDDLE_SPEAKERS=off` на `on` (если строки нет, добавь её;
  вторая такая строка ниже первой не сработает), потом модели (шаг 4) и перезапуск задачи
  «Huddle Notes». Без базы лиц в `данные\people` программа сначала никого не узнаёт.
- **Пожелание к заметкам** («пиши короче», «задачи всегда с датами»): допиши в конец
  `данные\пожелания.md` строку в том же виде, что и остальные:
  `- ДД.ММ.ГГГГ, <кто> (Claude Code): <пожелание>`. Если файла нет, создай его с заголовком
  `# Пожелания к заметкам`. Правь файл своим инструментом редактирования, в UTF-8, не через
  `Add-Content`. Покажи владельцу итоговый список. Действует со следующего звонка.
- **«Покажи пожелания», «убери это пожелание»:** пожелание — строка, начинающаяся с «- », в
  `данные\пожелания.md`; удаляй только то, что владелец назвал. В заметки идут 40 последних.
- **Расшифровка не на том языке** (например, звонок шёл на двух языках): программа определяет
  язык по трём кускам с 20, 50 и 80 % записи, тишина в начале ей не мешает. На каком языке
  расшифрован звонок, видно в логе в строке `<запись>: language ru, … words`. Чтобы пересчитать
  такой звонок, удали
  `данные\work\<имя записи без расширения>\words.json` (если такой папки нет, это та, где в
  `document.txt` стоит имя файла звонка в «Результатах») и обработай запись вручную (см. выше),
  задав язык только для этой команды:
  ```powershell
  $env:HUDDLE_WHISPER_LANGUAGE = "en"; программа\.venv\Scripts\huddle-notes.exe process "Записи\<запись>" --notify; Remove-Item Env:HUDDLE_WHISPER_LANGUAGE
  ```
  В `.env` язык не закрепляй, если владелец не просит: звонок на другом языке тогда
  расшифруется с потерями.
- **Что-то не работает:** смотри `данные\logs\watch.log` и журнал в «Результатах».
  Всё про отзывы из Slack — строки со словом `feedback`.
- **«Заметки не получились» с `Login expired`:** вход в `claude` истёк. Владелец снова
  выполняет `claude auth login` (шаг 5). Заметки к этому звонку придут сами: программа
  просит их заново раз в 30 минут, до 6 часов.
- **«Обнови программу из папки <путь>»** (владелец распаковал архив «Обновление Huddle Notes»:
  в нём `программа\` и `CLAUDE.md`). Сначала прочитай `CLAUDE.md` из этой папки: в новой версии
  могут быть свои шаги. Потом из корня Huddle Notes; каждый блок — одной командой:
  ```powershell
  Stop-ScheduledTask -TaskName "Huddle Notes"
  $venv = (Resolve-Path программа\.venv -ErrorAction Stop).Path
  Get-Process | Where-Object { $_.Path -like "$venv\*" } | Select-Object Id, Name
  ```
  Список программ из `.venv` должен быть пустым; если нет, останови их (`Stop-Process -Id <Id>`),
  иначе установка не сможет заменить `huddle-notes.exe`. Потом, подставив в `$src` папку, где
  лежат `программа` и `CLAUDE.md` (Windows при распаковке часто кладёт её в одноимённую папку):
  ```powershell
  $src = "<папка обновления>"
  Copy-Item -Recurse -Force "$src\программа\*" программа\
  Copy-Item -Force "$src\CLAUDE.md" .
  cd программа
  uv pip install --python .venv\Scripts\python.exe --reinstall-package huddle-notes .
  cd ..
  Start-ScheduledTask -TaskName "Huddle Notes"
  ```
  Проверь, что в `данные\logs\watch.log` появилась новая строка `watching ...\Записи`.
  `.env`, `данные\`, «Записи» и «Результаты» обновление не трогает. Если в `данные\people`
  лежит старая база лиц и голосов, а имена выключены, она не используется; удаляй её, только
  если владелец попросит.

## Правила

- Записи, расшифровки, лица и голоса никуда не отправляй, кроме Slack-сообщения программы.
- Не показывай содержимое `.env` в чате.
- Не меняй код в `программа\`, пока владелец явно не попросит.
