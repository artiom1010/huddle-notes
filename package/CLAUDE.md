# Huddle Notes — инструкция для Claude

Эта папка делает заметки и расшифровки Slack-хадлов команды. Ты помогаешь её владельцу:
устанавливаешь всё один раз, отвечаешь на вопросы по звонкам и исправляешь имена.

```
Huddle Notes/
  Записи/       сюда OBS сохраняет видео хадлов
  Результаты/   один файл на звонок: Заметки + Расшифровка; .huddle-notes-journal.json — что обработано
  данные/       база команды (лица, голоса — это биометрия), словари, модели, work/, logs/
  программа/    код huddle-notes; не меняй его, пока об этом не попросили
  whisper/      whisper.cpp для Windows (появится при установке)
  .env          настройки и секреты; никогда не показывай токены в чате
```

Как это работает: программа-наблюдатель запускается при входе в Windows, замечает новую
запись в «Записи», делает расшифровку с именами (Whisper, подсвеченные плитки, голоса),
просит Claude Code (`claude -p`, без инструментов) написать заметки, кладёт файл в
«Результаты» и присылает сообщение в Slack.

## Установка (один раз, Windows, PowerShell)

Все команды — из корня этой папки. После каждой установки через winget открой новый терминал.

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
5. Claude Code должен быть залогинен (подписка владельца). Проверь:
   ```powershell
   claude -p "Ответь одним словом: работает" --tools ""
   ```
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
`processing ...`, потом в «Результатах» появится файл, а в Slack — сообщение. Покажи
владельцу время обработки из лога и спроси, правильно ли расставлены имена.

## Что делать по просьбам владельца

- **«Это Вася» про неузнанного человека.** Его лицо лежит в `Результаты\Не опознаны\<звонок>\Участник N.jpg`;
  N — номер группы. Найди запись этого звонка в «Записи» (по журналу) и запусти:
  ```powershell
  программа\.venv\Scripts\huddle-notes.exe process "Записи\<запись>" --names "N=Вася" --learn --notify
  ```
  Файл в «Результатах» перезапишется с именем, программа запомнит Васю, в Slack уйдёт исправленная версия.
- **«Обработай запись» вручную:** та же команда без `--names` и `--learn`.
- **Вопросы по звонкам** («что поручили Жене?», «где говорили про iOS?»): ищи в файлах
  «Результатов», отвечай со временем из расшифровки. Помни, что расшифровка бывает неточной;
  «?» значит, что говорящий не определён.
- **Что-то не работает:** смотри `данные\logs\watch.log` и журнал в «Результатах».

## Правила

- Записи, расшифровки, лица и голоса никуда не отправляй, кроме Slack-сообщения программы.
- Не показывай содержимое `.env` в чате.
- Не меняй код в `программа\`, пока владелец явно не попросит.
