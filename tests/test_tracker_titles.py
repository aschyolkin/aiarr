"""Formats observed in production search results (NNM-Club, BigFAN, Rutracker)."""

import pytest

from mae.config import Policy
from mae.domain import Evidence, ReleaseDetails, SearchResult, Work
from mae.parsing import parse_candidate, tracker_names, tracker_year
from mae.policies import evaluate

SILO = Work(kind="series", external_id=1, title="Укрытие", aliases=["Silo"], year=2023, seasons=[3],
            episodes=[f"S03E{n:02d}" for n in range(1, 11)])
GLADIATOR = Work(kind="movie", external_id=98, title="Гладиатор", aliases=["Gladiator"], year=2000)
HER = Work(kind="movie", external_id=152601, title="Она", aliases=["Her"], year=2013)


def _candidate(title, card=""):
    evidence = [Evidence(id="card", text=card, method="test")] if card else []
    return parse_candidate(
        SearchResult(provider="p", release_id="1", title=title, size=4 * 1024**3), ReleaseDetails(evidence=evidence)
    )


def test_names_skip_bracketed_facts_and_season_segments():
    title = "[SERIAL] Укрытие / Бункер / Silo / S3E1-10 of 10 [2026, США, драма, WEB-DL 1080p] + Original + )"
    assert tracker_names(title) == ["Укрытие", "Бункер", "Silo"]
    assert tracker_year(title) == 2026
    assert tracker_year("Она / Her (2013) BDRip 1080p") == 2013


def test_rutracker_series_title_matches_despite_guessit_title():
    c = evaluate(_candidate("Укрытие / Бункер / Silo / S3E1-10 of 10 [2026, США, WEB-DL 1080p] Dub + Original + )"),
                 SILO, Policy())
    assert c.parsed["title"] == "Original"  # guessit's guess; the name parts decide identity
    assert not c.reasons and not c.unknown


def test_rutracker_movie_year_comes_from_the_facts_block():
    c = evaluate(_candidate("Гладиатор / Gladiator 4K [Extended Cut] (Ридли Скотт / Ridley Scott) "
                            "[2000, США, боевик, UHD BDRip 2160p] AVO (Гаврилов)"), GLADIATOR, Policy())
    assert c.parsed["year"] == 2000 and not c.reasons and not c.unknown


def test_same_russian_name_of_another_year_is_still_another_movie():
    c = evaluate(_candidate("Она / She (2023) WEB-DL 1080p | D"), HER, Policy())
    assert "YEAR_UNVERIFIED" in c.reasons


@pytest.mark.parametrize("title", [
    "Она / Her (2013) BDRip 1080p | D, L, A-Лицензия",
    "Она / Her (2013) BDRip 1080p [MVO]",
    "Her 2013 DUB, 2 x MVO, AVO, Sub BDRip 1080p - RUSSIAN",
    "Она / Her (2013) BDRip 1080p от Generalfilm | D",
])
def test_tracker_translation_markers_mean_russian_audio(title):
    c = evaluate(_candidate(title), HER, Policy())
    assert "russian_audio" not in c.unknown


@pytest.mark.parametrize("title", ["Она / Her (2013) BDRip 1080p + Original + Sub", "Her.2013.1080p.WEB-DL.MULTI"])
def test_original_or_subtitles_alone_are_not_russian_audio(title):
    assert "russian_audio" in evaluate(_candidate(title), HER, Policy()).unknown


@pytest.mark.parametrize("line", [
    "Перевод: Профессиональный (многоголосый, закадровый)",
    "Перевод: Дублированный (MovieDalen)",
    "Аудио #1 Rus: AC3, 640 kbps, 6 ch - одноголосый закадровый",
])
def test_card_translation_lines_mean_russian_audio(line):
    c = evaluate(_candidate("Она / Her (2013) BDRip 1080p", card=line), HER, Policy())
    assert "russian_audio" not in c.unknown


@pytest.mark.parametrize("line", ["Перевод: отсутствует", "Перевод: неизвестен", "Аудио #2 Eng: AC3 - оригинал"])
def test_card_lines_without_russian_track(line):
    assert "russian_audio" in evaluate(_candidate("Она / Her (2013) BDRip 1080p", card=line), HER, Policy()).unknown


@pytest.mark.parametrize(("line", "size"), [
    ("Видео: MPEG-4 AVC, ~10,0 Мбит/с, 1918x802", "1080p"),
    ("Видео: НЕVC/H.265, 3840x1606, ~23,9 Mbps", "2160p"),
    ("Video: AVC, 1280x536, 23.976 fps", "720p"),
])
def test_resolution_comes_from_the_card_when_the_title_has_none(line, size):
    c = _candidate("Она / Her (2013) BDRip | D", card=line)
    assert c.parsed["screen_size"] == size


def test_title_resolution_wins_over_the_card():
    assert _candidate("Она / Her (2013) BDRip 720p | D", card="Видео: AVC, 1920x800").parsed["screen_size"] == "720p"


@pytest.mark.parametrize("title", [
    "Хью Хауи | Укрытие (Книга 1). Иллюзия (2023) [MP3, Станислав Федорчук]",
    "(Score) [CD] Гладиатор / Gladiator (Music From The Motion Picture) - 2025, FLAC (tracks), lossless",
    "The Amazing Spider-Man 2 / Новый Человек-Паук 2 (2014) [PS3] [EUR] [License] [Ru]",
    "Мэтт Грейнинг | Симпсоны. Антология. [Том 1-6] (2019-2021) [PDF, CBZ]",
])
def test_books_music_and_games_are_not_video(title):
    assert "NOT_VIDEO_RELEASE" in evaluate(_candidate(title), GLADIATOR, Policy()).reasons
