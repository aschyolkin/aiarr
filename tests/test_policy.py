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


def test_wrong_episode():
    work = Work(kind="series", external_id=9, title="Example", seasons=[1], episodes=["S01E02"])
    c = evaluate(candidate("Audio: Russian", "Example.S01E03.1080p.WEB-DL.H.264"), work, Policy())
    assert "EPISODE_MISMATCH" in c.reasons


def test_llm_quote_validation():
    evidence = [Evidence(id="1", text="Audio: Russian", method="fixture")]
    claim = {"field": "audio_language", "value": "rus", "evidence_id": "1", "quote": "Audio: Russian"}
    raw = json.dumps({"claims": [claim], "unknown": [], "contradictions": []})
    assert validate_interpretation(raw, evidence).claims[0].method == "deepseek-v4-flash"
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
