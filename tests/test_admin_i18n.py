import re

from mae.admin import ADMIN_HTML
from mae.admin_i18n import rules_json, translate

CYRILLIC = re.compile("[А-Яа-яЁё]")
HTML, SCRIPT = ADMIN_HTML.split("<script>", 1)
SCRIPT = SCRIPT.split("</script>", 1)[0].replace(rules_json(), "[]")
# Pieces of strings that are assembled in the script; the composite rules cover the result.
WORDS = {"источник", "разрешение", "кодек", "студия", "озвучка", "релиз", "фильма", "источнику", "источникам"}


def is_fragment(text):
    return text != text.strip() or text.startswith(". ") or text in WORDS


def static_texts():
    body = re.sub(r"<style>.*?</style>", "", HTML, flags=re.S)
    for text in re.findall(r">([^<>]*[А-Яа-яЁё][^<>]*)<", body):
        yield text
    for text in re.findall(r'(?:placeholder|title)="([^"]*[А-Яа-яЁё][^"]*)"', body):
        yield text


def test_static_page_is_fully_translated():
    left = [(t, translate(t)) for t in static_texts() if CYRILLIC.search(translate(t))]
    assert not left


def test_script_strings_are_translated():
    literals = re.findall(r"'([^'\n]*[А-Яа-яЁё][^'\n]*)'", SCRIPT)
    assert len(literals) > 100
    left = [(t, translate(t)) for t in literals if CYRILLIC.search(translate(t)) and not is_fragment(t)]
    assert not left


def test_composed_sentences():
    samples = {
        "Найдено 3 раздач по запросу «Дюна».": "Found 3 releases for “Дюна”.",
        "Заявка принята · готово 2 из 8 серий · ожидаются E03, E04 · попыток: 1":
            "Request received · 2 of 8 episodes ready · pending E03, E04 · attempts: 1",
        "✅ Выбран · 40 баллов (источник +40, разрешение +10)":
            "✅ Chosen · 40 points (source +40, resolution +10)",
        "Jellyfin ответил ошибкой HTTP 500 — задача ждёт, попытки не расходуются":
            "Jellyfin returned HTTP 500 — the job waits, attempts are not used up",
        "💾 свободно 120 ГиБ из 900 ГиБ (ещё 30 ГиБ докачивается)":
            "💾 120 GiB free of 900 GiB (30 GiB still downloading)",
        "Файл E05 добавлен в библиотеку.": "Episode E05 was added to the library.",
        "ошибка: 2, лимит запросов: 1": "error: 2, rate limited: 1",
        "1.2 с": "1.2 s", "340 мс": "340 ms", "нет": "none",
        "Поиск идёт по 2 источникам.": "Searching 2 source(s).",
        "Проверено 7 вариантов; требованиям соответствуют 2.": "Checked 7 candidates; 2 meet the requirements.",
        "Выбран релиз: X (rutor). Подтверждено: audio_language: rus": "Chosen release: X (rutor). Confirmed: audio_language: rus",
        "Почему он: 40 баллов (источник +40); других подходящих — 2. При равных баллах решают разрешение из основного списка и число сидов.":
            "Why this one: 40 points (source +40); other suitable ones: 2. On a tie the resolution from the main list and the number of seeds decide.",
        "Найден релиз лучше: «X» (rutor). Сейчас 1080p · 30 баллов → будет 2160p · 60 баллов (разрешение +30).":
            "A better release was found: «X» (rutor). Currently 1080p · 30 points → will be 2160p · 60 points (resolution +30).",
        "Поиск раздач идёт по 3 подключённым источникам aiarr. Из списка трекеров подключено 2 из 5; четыре студии озвучки распознаются в найденных раздачах.":
            "Releases are searched in 3 sources connected to aiarr. 2 of the 5 trackers in the list are connected; the four dubbing studios are recognised in the releases found.",
    }
    for source, expected in samples.items():
        assert translate(source) == expected
