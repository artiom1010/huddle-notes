import json
from pathlib import Path

from huddle_notes.domain import Word
from huddle_notes.transcribe import WhisperTranscriber, read_words


def tok(text: str, ms: int, dtw: int = -1) -> dict[str, object]:
    return {"text": text, "offsets": {"from": ms, "to": ms + 200}, "t_dtw": dtw}


def seg(text: str, start_ms: int, end_ms: int, tokens: list[dict[str, object]]) -> dict[str, object]:
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}, "tokens": tokens}


def write(tmp_path: Path, segments: list[dict[str, object]]) -> Path:
    path = tmp_path / "words.json"
    path.write_text(json.dumps({"transcription": segments}, ensure_ascii=False), encoding="utf-8")
    return path


def test_glues_tokens_into_words_and_ends_each_word_where_the_next_starts(tmp_path):
    path = write(
        tmp_path,
        [
            seg(
                " Привет всем", 0, 2000, [tok("[_BEG_]", 0), tok(" При", 0, 10), tok("вет", 300, 40), tok(" всем", 600)]
            ),
            seg(" Начнём", 2500, 4000, [tok(" Начнём", 2500, 260)]),
        ],
    )
    assert read_words(path) == [
        Word(0.1, 0.6, "Привет"),  # word timing from dtw; ends where "всем" starts (no dtw: token offset)
        Word(0.6, 2.0, "всем"),  # last word of its segment ends with the segment...
        Word(2.6, 4.0, "Начнём"),  # ...unless the next word starts earlier
    ]


def test_drops_whisper_loops_and_stock_phrases(tmp_path):
    path = write(
        tmp_path,
        [
            seg(" Давайте начнём планёрку", 0, 2000, [tok(" Давайте", 0), tok(" начнём", 500), tok(" планёрку", 900)]),
            seg(" Давайте начнём планёрку", 2000, 4000, [tok(" Давайте", 2000), tok(" начнём", 2500)]),
            seg(
                " Субтитры сделал корректор",
                4000,
                6000,
                [tok(" Субтитры", 4000), tok(" сделал", 4500), tok(" корректор", 5000)],
            ),
        ],
    )
    assert [w.text for w in read_words(path)] == ["Давайте", "начнём", "планёрку", "сделал"]


def test_short_words_still_last_a_little(tmp_path):
    path = write(tmp_path, [seg(" Да", 0, 1000, [tok(" Да", 1000)])])
    assert read_words(path) == [Word(1.0, 1.05, "Да")]


def test_command_passes_the_glossary_as_a_prompt_without_a_shell():
    cmd = WhisperTranscriber("whisper-cli", Path("m.bin"), "large.v3.turbo").command(
        Path("a.wav"), Path("out/words"), "Трактор; rm -rf /"
    )
    assert cmd[0] == "whisper-cli"
    assert cmd[cmd.index("--prompt") + 1] == "Трактор; rm -rf /"
    assert cmd[cmd.index("-dtw") + 1] == "large.v3.turbo"
    assert cmd[cmd.index("-of") + 1] == "out/words"
