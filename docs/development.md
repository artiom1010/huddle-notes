# Для разработчиков

## Как устроен код

| Модуль (`huddle_notes/`) | Что делает |
|---|---|
| `domain.py` | данные звонка: слова, группы аватарок, реплики |
| `speakers/tiles.py` | поиск подсвеченной плитки на одном кадре |
| `speakers/detector.py` | плитки по всему видео → группы по аватаркам (`speakers.json`) |
| `transcribe.py` | запуск whisper.cpp и чтение его ответа |
| `voices.py` | отпечатки голосов (sherpa-onnx) |
| `people.py` | база аватарок и голосов команды |
| `attribution.py` | кто сказал каждое слово: плитка → аватарка → голос → «?» |
| `render.py` | один документ на звонок (Заметки + Расшифровка) и отчёт |
| `pipeline.py` | один звонок целиком; каждый шаг сохраняет результат и продолжает после сбоя |
| `regression.py` | проверка, что новая версия называет людей так же, как принятая |
| `models.py` | каталог моделей: файлы, ссылки, контрольные суммы |
| `config.py` | все настройки из окружения (`.env`), проверка до начала работы |
| `cli.py` | команды и сборка объектов; больше никто не читает окружение |

Внешние программы (ffmpeg, whisper) запускаются списком аргументов, без shell, с
ограничением по времени. Имена людей никогда не становятся путями к файлам.

## Настройки

Все настройки — переменные окружения, на сервере они в файле `.env`. Полный список с
пояснениями — в [`.env.example`](../.env.example). Модели выбираются там же, каталог
вариантов — в [`huddle_notes/models.py`](../huddle_notes/models.py).

Данные лежат вне репозитория, в `HUDDLE_DATA_DIR`:

```
data/
  people/        avatars.json, voices.json, faces/   ← биометрия, только на сервере
  glossaries/    <имя>.txt — словарь для whisper: имена, проекты, термины команды
  models/        файлы моделей (huddle-notes models download)
```

## Запуск на Маке

```bash
brew install ffmpeg whisper-cpp
uv venv --python 3.12.16 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env                    # HUDDLE_DATA_DIR — своя папка, HUDDLE_HWACCEL=videotoolbox
set -a; . ./.env; set +a
.venv/bin/huddle-notes models download  # скачивает выбранные модели и проверяет контрольные суммы
.venv/bin/huddle-notes process "запись.mov" --glossary команда
```

Рядом с записью появится папка с одним файлом `ГГГГ-ММ-ДД ЧЧ-ММ.md` и `_work/` (звук,
ответ whisper, найденные плитки). Повторный запуск не пересчитывает готовые шаги.

Если кого-то не узнали, его лицо будет в папке `Не опознаны`, а в отчёте — номер группы.
Назвать и запомнить:

```bash
.venv/bin/huddle-notes process "запись.mov" --glossary команда --names '{"3": "Вася"}' --learn
```

## Проверки

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest
.venv/bin/huddle-notes regress <папка эталона>
```

`regress` прогоняет звонки из папки эталона (вне git: там записи и голоса) и сравнивает
расшифровку с принятой. Любое отличие — ошибка. Задуманное улучшение смотрят глазами и
принимают: `regress <папка> --accept`. С `HUDDLE_REGRESSION_DIR=<папка>` эта же проверка
входит в `pytest`.

На GitHub при каждом изменении сами проверяются стиль, типы, тесты, `deploy.sh`, сборка
Docker и случайно попавшие в код токены.

## Как вносить изменения

Ветка → pull request с описанием по-русски → проверки зелёные → ревью → слияние в `main`
→ метка версии (`v0.2.0`) → выкладка.

## Сервер

Работает в Docker, данные в `/srv/huddle-notes/` на сервере:

```bash
docker compose run --rm worker models download
docker compose run --rm worker process /calls/<запись>.mov --glossary команда
```

Выкладка версии — сборка, проверка на эталоне, откат, если что-то изменилось:

```bash
./deploy.sh v0.2.0
```
