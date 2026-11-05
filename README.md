# huddle-notes

Расшифровка звонков Slack (хадлов) с именами говорящих. Запись окна хадла →
`Расшифровка.md`, где у каждой реплики стоит имя.

Кто говорит, определяется так:
1. по подсвеченной плитке на видео, человек узнаётся по аватарке;
2. по голосу там, где плитки не видно (демонстрация экрана, свёрнутое окно),
   и для проверки плитки;
3. если ни то, ни другое не уверено, ставится «?».

Текст даёт [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (модель large-v3-turbo),
голоса сравнивает модель wespeaker через [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).
Всё работает локально: звук и видео никуда не отправляются.

## Что где лежит

| Код (`huddle_notes/`) | Что делает |
|---|---|
| `domain.py` | слова, группы аватарок, реплики — только данные |
| `speakers/tiles.py` | поиск плитки говорящего на одном кадре |
| `speakers/detector.py` | плитки по всему видео → группы по аватаркам (`speakers.json`) |
| `transcribe.py` | запуск whisper и чтение его ответа |
| `voices.py` | отпечатки голосов |
| `people.py` | база аватарок и голосов команды |
| `attribution.py` | кто сказал каждое слово |
| `render.py` | текст расшифровки и отчёт |
| `pipeline.py` | один звонок целиком, с продолжением после сбоя |
| `regression.py` | проверка, что новая версия называет людей так же |
| `config.py`, `cli.py` | настройки и команды |

**Данные в git не попадают никогда.** Записи, база голосов (это биометрия),
глоссарии и модели лежат в папке данных (`HUDDLE_DATA_DIR`):

```
data/
  people/        avatars.json, voices.json, faces/
  glossaries/    <имя>.txt — подсказка для whisper: имена, проекты, термины команды
  models/        ggml-large-v3-turbo.bin, wespeaker_en_voxceleb_resnet34.onnx
```

## Запуск на Маке

```bash
brew install ffmpeg whisper-cpp
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # пути к данным; на Маке HUDDLE_HWACCEL=videotoolbox
scripts/download-models.sh      # модели в HUDDLE_MODELS_DIR, с проверкой контрольных сумм
set -a; . ./.env; set +a
.venv/bin/huddle-notes process "запись.mov" --glossary команда
```

Рядом с записью появится папка с `Расшифровка.md` и `_work/` (звук, ответ whisper,
найденные плитки). Повторный запуск не пересчитывает готовые шаги.

Если кого-то не узнали, его лицо будет в папке `Не опознаны`, а в отчёте номер группы.
Назвать и запомнить:

```bash
.venv/bin/huddle-notes process "запись.mov" --glossary команда --names '{"3": "Вася"}' --learn
```

## Проверки

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest
.venv/bin/huddle-notes regress ../Huddles/_regression
```

`regress` прогоняет звонки из папки эталона (вне git) и сравнивает расшифровку с принятой.
Любое отличие — ошибка. Если отличие — задуманное улучшение, его смотрят глазами и
принимают: `regress <папка> --accept`.

На GitHub при каждом изменении сами запускаются стиль, типы, тесты, сборка Docker и
поиск случайно попавших в код токенов.

## Сервер

Работает в Docker (`docker compose`), данные в `/srv/huddle-notes/` на сервере.
Выкладка версии одной командой — сборка, проверка на эталоне и откат, если стало хуже:

```bash
./deploy.sh v0.1.0
```

## Как вносить изменения

Ветка → pull request с описанием по-русски → проверки зелёные → ревью → слияние в `main`
→ метка версии → `./deploy.sh <версия>` на сервере.
