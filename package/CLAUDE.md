# Huddle Notes — инструкция для Claude

Эта папка делает заметки и расшифровки Slack-хадлов команды. Ты помогаешь её владельцу:
устанавливаешь всё один раз, отвечаешь на вопросы по звонкам, исправляешь имена и
записываешь пожелания к заметкам.

```
Huddle Notes/
  Записи/       сюда OBS сохраняет видео хадлов
  Результаты/   один файл на звонок: Заметки + Расшифровка; .huddle-notes-journal.json — что обработано
  данные/       база команды (лица, голоса — это биометрия), словари, модели, work/, logs/;
                пожелания.md — пожелания команды к заметкам
  программа/    код huddle-notes; не меняй его, пока об этом не попросили
  whisper/      whisper.cpp для Windows (появится при установке)
  .env          настройки и секреты; никогда не показывай токены в чате
```

Как это работает: программа-наблюдатель запускается при входе в Windows, замечает новую
запись в «Записи», делает расшифровку с именами (Whisper, подсвеченные плитки, голоса),
просит Claude Code (`claude -p`, без инструментов) написать заметки, кладёт файл в
«Результаты» и присылает сообщение в Slack. Раз в 5 минут он читает ответы в треде под своими
сообщениями: каждый ответ становится пожеланием в `данные\пожелания.md`, а бот отвечает в треде
«Записал». На 👎 без слов бот один раз спрашивает в треде, что не так. Пожелания Claude получает
вместе с каждой новой расшифровкой; уже готовые заметки они не меняют.

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
   У выбранных моделей должно стоять `downloaded`.
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
быть «Заметки не получились». Покажи владельцу время обработки из лога и спроси, правильно ли
расставлены имена.

## Что делать по просьбам владельца

- **«Это Вася» про неузнанного человека.** Снимок окна, где он обведён красной рамкой, лежит в
  `Результаты\Не опознаны\<звонок>\Участник N.jpg`;
  N — номер группы. Найди запись этого звонка в «Записи» (по журналу) и запусти:
  ```powershell
  программа\.venv\Scripts\huddle-notes.exe process "Записи\<запись>" --names "N=Вася" --learn --notify
  ```
  Файл в «Результатах» перезапишется с именем, программа запомнит Васю, в Slack уйдёт исправленная версия.
- **«Обработай запись» вручную:** та же команда без `--names` и `--learn`.
- **Вопросы по звонкам** («что поручили Жене?», «где говорили про iOS?»): ищи в файлах
  «Результатов», отвечай со временем из расшифровки. Помни, что расшифровка бывает неточной;
  «?» значит, что говорящий не определён.
- **Пожелание к заметкам** («пиши короче», «задачи всегда с датами»): допиши в конец
  `данные\пожелания.md` строку в том же виде, что и остальные:
  `- ДД.ММ.ГГГГ, <кто> (Claude Code): <пожелание>`. Если файла нет, создай его с заголовком
  `# Пожелания к заметкам`. Покажи владельцу итоговый список. Действует со следующего звонка.
- **«Покажи пожелания», «убери это пожелание»:** пожелание — строка, начинающаяся с «- », в
  `данные\пожелания.md`; удаляй только то, что владелец назвал. В заметки идут 40 последних.
  Если там поправка имени («Участник 3 — это Вася»), предложи исправить звонок, как выше.
- **Что-то не работает:** смотри `данные\logs\watch.log` и журнал в «Результатах».
  Ошибки чтения отзывов в логе начинаются с `feedback not read`.
- **«Обнови программу»** (владелец распаковал архив обновления в эту папку с заменой файлов):
  ```powershell
  Stop-ScheduledTask -TaskName "Huddle Notes"
  $venv = (Resolve-Path программа\.venv).Path
  Get-Process | Where-Object { $_.Path -like "$venv\*" } | Select-Object Id, Name
  ```
  Список программ из `.venv` должен быть пустым; если нет, останови их (`Stop-Process -Id <Id>`),
  иначе установка не сможет заменить `huddle-notes.exe`. Потом:
  ```powershell
  cd программа
  uv pip install --python .venv\Scripts\python.exe --reinstall-package huddle-notes .
  cd ..
  Start-ScheduledTask -TaskName "Huddle Notes"
  ```
  Проверь, что в `данные\logs\watch.log` появилась новая строка `watching ...\Записи`.
- **«Заметки не получились» с `Login expired`:** вход в `claude` истёк. Владелец снова
  выполняет `claude auth login` (шаг 5). Заметки к этому звонку придут сами: программа
  просит их заново раз в 30 минут, до 6 часов.

## Правила

- Записи, расшифровки, лица и голоса никуда не отправляй, кроме Slack-сообщения программы.
- Не показывай содержимое `.env` в чате.
- Не меняй код в `программа\`, пока владелец явно не попросит.
