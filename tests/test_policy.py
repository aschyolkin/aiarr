import json

import pytest

from mae.config import Policy
from mae.deepseek import validate_interpretation
from mae.domain import Evidence, ReleaseDetails, SearchResult, Work
from mae.parsing import parse_candidate
from mae.policies import evaluate
from mae.security import clean_text


def candidate(description, title="Example.2024.1080p.WEB-DL.H.264.MULTI"):
    return parse_candidate(
        SearchResult(provider="fixture", release_id="1", title=title, size=1_000_000),
        ReleaseDetails(evidence=[Evidence(id="description", text=description, method="fixture")]),
    )


@pytest.mark.parametrize(
    "description",
    [
        "",
        "MULTI",
        "Subtitles: Russian",
        "Субтитры: русские",
        "RUS",
        "Text\nLanguage : Russian",
        "Audio\nLanguage : English\nText\nLanguage : Russian",
        "Озвучка: неизвестна",
        "Оригинал + субтитры",
        "Audio: English; subtitles Russian",
    ],
)
def test_no_russian_from_subtitles_or_title(description, movie):
    c = evaluate(candidate(description), movie, Policy())
    assert "russian_audio" in c.unknown
    assert not c.score


@pytest.mark.parametrize("description", ["Audio: Russian", "Аудио: русский", "Audio #1\nLanguage : Russian"])
def test_scoped_russian(description, movie):
    c = evaluate(candidate(description), movie, Policy())
    assert not c.unknown and not c.reasons and c.score


def test_numbered_russian_audio_and_video_on_source_card(movie):
    c = evaluate(
        candidate(
            "Видео: MPEG-4 AVC, 1918x802\n"
            "Аудио #1: Русский: AC3, 6 channels\n"
            "Аудио #2: Английский: AC3, 6 channels",
            "Example.2024.1080p.WEB-DL",
        ),
        movie,
        Policy(),
    )
    assert not c.unknown and not c.reasons and c.score
    assert c.parsed["video_codec"] == "H.264"
    assert any(claim.field == "audio_language" and claim.value == "rus" for claim in c.claims)


def test_source_card_codec_disagreement_is_rejected(movie):
    c = evaluate(
        candidate("Видео: MPEG-4 AVC\nАудио #1: Русский", "Example.2024.1080p.WEB-DL.H.265"),
        movie,
        Policy(),
    )
    assert "CODEC_EVIDENCE_CONFLICT" in c.reasons
    assert not c.score


@pytest.mark.parametrize("studio", ["LostFilm", "NewStudio", "AlexFilm", "Red Head Sound", "Flarrow Films"])
def test_studios_equal_default_weight(studio, movie):
    c = evaluate(candidate("Аудио: русский " + studio), movie, Policy())
    assert any(x.field == "studio" and x.value == studio for x in c.claims)
    assert c.score["studio"] == 0


@pytest.mark.parametrize("alias", ["R.H.S.", "РХС"])
def test_rhs_audio_aliases_are_context_scoped(alias, movie):
    c = evaluate(candidate("Аудио: русский " + alias), movie, Policy())
    assert any(claim.field == "studio" and claim.value == "Red Head Sound" for claim in c.claims)
    title_only = evaluate(candidate(alias), movie, Policy())
    assert not any(claim.field == "studio" for claim in title_only.claims)


@pytest.mark.parametrize(
    "title,reason",
    [
        ("Other.2024.1080p.WEB-DL.H.264", "IDENTITY_MISMATCH"),
        ("Example.1984.1080p.WEB-DL.H.264", "YEAR_UNVERIFIED"),
        ("Example.2024.1080p.CAM.H.264", "SOURCE_NOT_ALLOWED"),
    ],
)
def test_constraints_before_score(title, reason, movie):
    c = evaluate(candidate("Audio: Russian", title), movie, Policy())
    assert reason in c.reasons and not c.score


@pytest.mark.parametrize(
    "title",
    [
        "Example.2024.Hybrid.1080p.H.264",  # unrecognized/unmapped source label
        "Example.2024.DVDScr.1080p.H.264",  # maps to an ordinary source (DVD), not banned
        "Example.2024.1080p.H.264",  # no source label at all
    ],
)
def test_unrecognized_or_unlabeled_source_is_accepted_for_review(title, movie):
    c = evaluate(candidate("Audio: Russian", title), movie, Policy())
    assert "SOURCE_NOT_ALLOWED" not in c.reasons
    assert c.score.get("source") == 0


@pytest.mark.parametrize(
    "title",
    [
        "Example.2024.CamRip.1080p.H.264",
        "Example.2024.HDCAM.1080p.H.264",
        "Example.2024.TS.1080p.H.264",
        "Example.2024.HDTS.1080p.H.264",
        "Example.2024.TC.1080p.H.264",
        "Example.2024.Telecine.1080p.H.264",
    ],
)
def test_explicit_cam_telesync_telecine_sources_are_rejected(title, movie):
    c = evaluate(candidate("Audio: Russian", title), movie, Policy())
    assert "SOURCE_NOT_ALLOWED" in c.reasons


def test_wrong_episode():
    work = Work(kind="series", external_id=9, title="Example", seasons=[1], episodes=["S01E02"])
    c = evaluate(candidate("Audio: Russian", "Example.S01E03.1080p.WEB-DL.H.264"), work, Policy())
    assert "EPISODE_MISMATCH" in c.reasons


def test_pack_can_cover_missing_episode_only_when_other_episodes_are_monitored_and_covered():
    title = "Example.S01E01-E02.1080p.WEB-DL.H.264"
    work = Work(
        kind="series", external_id=9, title="Example", seasons=[1],
        episodes=["S01E02"], covered_episodes=["S01E01"],
    )
    accepted = evaluate(candidate("Audio: Russian", title), work, Policy())
    assert not accepted.reasons and not accepted.unknown and accepted.score
    rejected = evaluate(candidate("Audio: Russian", title), work.model_copy(update={"covered_episodes": []}), Policy())
    assert "EPISODE_MISMATCH" in rejected.reasons


def test_llm_quote_validation():
    evidence = [Evidence(id="1", text="Audio: Russian", method="fixture")]
    claim = {"field": "audio_language", "value": "rus", "evidence_id": "1", "quote": "Audio: Russian"}
    raw = json.dumps({"claims": [claim], "unknown": [], "contradictions": []})
    assert validate_interpretation(raw, evidence).claims[0].method == "deepseek-flash"
    for change in [
        {"quote": "invented"},
        {"evidence_id": "missing"},
        {"value": "made-up"},
        {"field": "shell"},
    ]:
        with pytest.raises(ValueError):
            validate_interpretation(
                json.dumps({"claims": [{**claim, **change}], "unknown": [], "contradictions": []}), evidence
            )
    with pytest.raises(ValueError):
        validate_interpretation("{broken", evidence)


def test_secrets_removed():
    raw = (
        "<script>bad()</script>Audio: Russian\nCookie: secret\nhttps://site/dl?passkey=xyz\nuser@example.com"
    )
    cleaned = clean_text(raw)
    assert all(s not in cleaned for s in ["bad()", "secret", "passkey", "xyz", "user@example.com"])
