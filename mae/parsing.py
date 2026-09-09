import re

from guessit import guessit

from mae.domain import Candidate, Claim, Evidence

STUDIOS = {
    "LostFilm": r"\blost[ ._-]?film\b",
    "NewStudio": r"\bnew[ ._-]?studio\b",
    "AlexFilm": r"\balex[ ._-]?film\b",
    "Red Head Sound": r"\bred[ ._-]?head[ ._-]?sound\b|\bR\.?H\.?S\.?\b|РХС",
    "Flarrow Films": r"\bflarrow[ ._-]?films?\b",
}


def normalize_title(value):
    return re.sub(r"[^\w]", "", value.casefold())


def parse_candidate(release, details):
    raw = guessit(release.title[:1000])
    fields = ("title", "year", "season", "episode", "screen_size", "source", "video_codec", "type")
    parsed = {key: raw[key] for key in fields if key in raw}
    c = Candidate(
        release=release,
        parsed=parsed,
        evidence=[Evidence(id="title", text=release.title, method="guessit-4.4.0"), *details.evidence],
    )
    # Only explicitly scoped audio evidence; MULTI/RUS title suffix is insufficient.
    for evidence in details.evidence:
        lines = evidence.text.splitlines()
        audio_block = False
        for line in lines:
            if re.match(r"(?i)^\s*(Audio|Аудио)(?:\s*#?\d+)?\s*$", line):
                audio_block = True
                continue
            if re.match(r"(?i)^\s*(Text|Video|General|Menu|Субтитры|Видео)(?:\s*#?\d+)?\s*$", line):
                audio_block = False
            direct_audio = bool(
                re.match(r"(?i)^\s*(аудио(?:дорожка)?|audio(?: track)?|звук)\s*\d*\s*[:：]", line)
            )
            audio_language = audio_block and bool(re.match(r"(?i)^\s*(language|язык)\s*:", line))
            if not (direct_audio or audio_language):
                continue
            # An audio and subtitle clause can share one line.
            line = re.split(r"(?i)(?:subtitles?|субтитры|text)\s*[:：]?", line, maxsplit=1)[0]
            if re.search(r"(?i)\b(?:not|no|without|нет|без|отсутствует)\b", line):
                continue
            if re.search(r"(?i)\b(rus|russian|русский|русская)\b", line):
                c.claims.append(
                    Claim(field="audio_language", value="rus", evidence_id=evidence.id, quote=line[:500])
                )
            if direct_audio:
                for studio, pattern in STUDIOS.items():
                    if re.search(pattern, line, re.I):
                        c.claims.append(
                            Claim(field="studio", value=studio, evidence_id=evidence.id, quote=line[:500])
                        )
    return c


def episode_keys(parsed):
    season = parsed.get("season")
    episodes = parsed.get("episode")
    if not isinstance(season, int) or episodes is None:
        return []
    if isinstance(episodes, int):
        episodes = [episodes]
    return [f"S{season:02d}E{ep:02d}" for ep in episodes if isinstance(ep, int)]
