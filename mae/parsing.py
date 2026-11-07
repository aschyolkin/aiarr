import re

from guessit import guessit

from mae.domain import Candidate, Claim, Evidence
from mae.verification import resolution_class

STUDIOS = {
    "LostFilm": r"\blost[ ._-]?film\b",
    "NewStudio": r"\bnew[ ._-]?studio\b",
    "AlexFilm": r"\balex[ ._-]?film\b",
    "Red Head Sound": r"\bred[ ._-]?head[ ._-]?sound\b|\bR\.?H\.?S\.?\b|РХС",
    "Flarrow Films": r"\bflarrow[ ._-]?films?\b",
    "HDRezka": r"\bhd[ ._-]?rezka\b",
    "Кубик в Кубе": r"кубик[ ._-]+в[ ._-]+кубе|\bkubik[ ._-]?v[ ._-]?kube\b",
    "TVShows": r"\btv[ ._-]?shows\b",
    "Jaskier": r"\bjaskier\b",
    "Пифагор": r"пифагор|\bpifagor\b",
    "Novamedia": r"\bnova[ ._-]?media\b",
    "Кураж-Бамбей": r"кураж[ ._-]?бамбей|\bkurazh[ ._-]?bambey\b",
    "SDI Media": r"\bsdi[ ._-]?media\b",
}
# Titles are noisier than audio lines: no bare abbreviations such as RHS.
TITLE_STUDIOS = {**STUDIOS, "Red Head Sound": r"\bred[ ._-]?head[ ._-]?sound\b"}
VOICES = (
    ("Dub", r"(?i)(?<![\w-])dub(?![\w-])|дубляж|дублир"),
    ("MVO", r"(?i)(?<![\w-])mvo(?![\w-])|многоголос"),
    ("DVO", r"(?i)(?<![\w-])dvo(?![\w-])|двухголос"),
    ("AVO", r"(?i)(?<![\w-])avo(?![\w-])|авторск"),
    ("VO", r"(?i)(?<![\w-])vo(?![\w-])|одноголос"),
)
# Rutracker-style title suffix codes: '| D, P2, A'.
VOICE_CODES = {"D": "Dub", "P": "MVO", "P2": "DVO", "P1": "VO", "A": "AVO", "L": "VO", "L1": "VO", "L2": "VO"}


def studio_pattern(name):
    """A custom studio name from the policy, matched as whole words."""
    return r"(?i)(?<!\w)" + r"[ ._-]?".join(re.escape(part) for part in name.split()) + r"(?!\w)"


def voices_in(text):
    found = [voice for voice, pattern in VOICES if re.search(pattern, text)]
    suffix = re.search(r"\|\s*((?:D|P\d?|L\d?|A|Sub)(?:\s*,\s*(?:D|P\d?|L\d?|A|Sub))*)\s*$", text)
    if suffix:
        found += [VOICE_CODES[code.strip()] for code in suffix[1].split(",") if code.strip() in VOICE_CODES]
    return list(dict.fromkeys(found))

# Russian trackers mark the translation in the title; on these trackers every
# such marker denotes a Russian track (VO = single voice-over, D/P/L = dubbing,
# professional, licensed). "Original" and subtitle markers are not audio.
TITLE_TRANSLATION = re.compile(
    r"(?<![\w-])(?:\d+\s*x\s*)?(?:Dub|MVO|DVO|AVO|VO|Дубляж|Дублированный)(?![\w-])|(?<!\w)Лицензия(?!\w)"
    r"|\|\s*(?:D|P\d?|L\d?|A)(?:\s*,\s*(?:D|P\d?|L\d?|A|Sub))*\s*$",
)
# Season/episode segments of 'Name / Name / S2E1-10 of 10' style titles.
SEGMENT_MARKER = re.compile(
    r"(?i)^(?:S\d{1,2}(?:E\d{1,4}.*)?|E\d{1,4}.*|сезон.*|серии.*|\d{1,2}\s*сезон.*|season.*)$"
)
NOT_VIDEO = re.compile(
    r"(?i)(?<![\w])(?:mp3|flac|m4b|pdf|epub|fb2|djvu|mobi|cbr|cbz|аудиокнига|саундтрек|soundtrack|"
    r"ps[2-5]|xbox\s*(?:one|360|series)?|apk)(?![\w])"
)


def normalize_title(value):
    return re.sub(r"[^\w]", "", value.casefold())


def tracker_names(title):
    """Names of a 'Рус / Alt / Eng (...) [...]' title, before any bracketed facts."""
    clean = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", title)
    head = re.split(r"\s*[\[(]|\s\|\s", clean, maxsplit=1)[0]
    return [
        part for part in (p.strip(" .-") for p in re.split(r"\s+/\s+", head))
        if part and not SEGMENT_MARKER.match(part)
    ][:6]


def tracker_year(title):
    """Year from '(2013)' or a '[2026, США, ...]' facts block."""
    match = re.search(r"\[(\d{4})(?:\s*[-–]\s*\d{4})?\s*[,\]]", title) or re.search(r"\((\d{4})\)", title)
    year = int(match[1]) if match else None
    return year if year and 1900 <= year <= 2100 else None


def _video_line_facts(line):
    codecs = set()
    if re.search(r"(?i)\b(?:h[ ._-]?264|x264|avc)\b", line):
        codecs.add("H.264")
    if re.search(r"(?i)\b(?:h[ ._-]?265|x265|hevc)\b", line):
        codecs.add("H.265")
    resolutions = {
        resolution_class(int(w), int(h))
        for w, h in re.findall(r"(?<!\d)(\d{3,4})\s*[xх×]\s*(\d{3,4})(?!\d)", line)
    }
    return codecs, resolutions


def parse_candidate(release, details, studios=()):
    """studios: extra policy studio names to recognize beyond STUDIOS."""
    extra = {name: studio_pattern(name) for name in studios if name not in STUDIOS}
    raw = guessit(release.title[:1000])
    fields = ("title", "year", "season", "episode", "screen_size", "source", "video_codec", "type")
    parsed = {key: raw[key] for key in fields if key in raw}
    names = tracker_names(release.title)
    if names:
        parsed["names"] = names
    if parsed.get("year") is None and (year := tracker_year(release.title)):
        parsed["year"] = year
    if parsed.get("source") is None and re.search(r"(?i)\b(?:bd|blu-?ray)\s*-?remux\b", release.title):
        parsed["source"] = "Blu-ray"
    if NOT_VIDEO.search(release.title):
        parsed["not_video"] = True
    c = Candidate(
        release=release,
        parsed=parsed,
        evidence=[Evidence(id="title", text=release.title, method="guessit-4.4.0"), *details.evidence],
        image_url=details.image_url,
    )
    if marker := TITLE_TRANSLATION.search(release.title):
        c.claims.append(Claim(field="audio_language", value="rus", evidence_id="title", quote=marker[0].strip()))
        for voice in voices_in(release.title):
            c.claims.append(Claim(field="voice", value=voice, evidence_id="title", quote=marker[0].strip()))
    for studio, pattern in {**TITLE_STUDIOS, **extra}.items():
        if found := re.search(pattern, release.title, re.I):
            c.claims.append(Claim(field="studio", value=studio, evidence_id="title", quote=found[0]))

    def line_facts(evidence, line):
        for studio, pattern in {**STUDIOS, **extra}.items():
            if re.search(pattern, line, re.I):
                c.claims.append(Claim(field="studio", value=studio, evidence_id=evidence.id, quote=line[:500]))
        for voice in voices_in(line):
            c.claims.append(Claim(field="voice", value=voice, evidence_id=evidence.id, quote=line[:500]))
    # Only explicitly scoped audio evidence; MULTI/RUS title suffix is insufficient.
    card_codecs, card_resolutions = set(), set()
    for evidence in details.evidence:
        lines = evidence.text.splitlines()
        audio_block = False
        for line in lines:
            video = re.match(r"(?i)^\s*(?:Video|Видео|Размер кадра)\s*[:：]\s*(.+)", line)
            if video:
                codecs, resolutions = _video_line_facts(video[1])
                card_codecs.update(codecs)
                card_resolutions.update(resolutions)
                for codec in codecs:
                    c.claims.append(
                        Claim(field="video_codec", value=codec, evidence_id=evidence.id, quote=line[:500])
                    )
                for resolution in resolutions - {None}:
                    c.claims.append(
                        Claim(field="resolution", value=str(resolution), evidence_id=evidence.id, quote=line[:500])
                    )
            if re.match(r"(?i)^\s*(Audio|Аудио)(?:\s*#?\d+)?\s*$", line):
                audio_block = True
                continue
            if re.match(r"(?i)^\s*(Text|Video|General|Menu|Субтитры|Видео)(?:\s*#?\d+)?\s*$", line):
                audio_block = False
            translation = re.match(r"(?i)^\s*(?:перевод|озвучка|озвучивание)\s*(?:#?\d+)?\s*[:：]\s*(\S.*)", line)
            if translation:
                # 'Перевод: Профессиональный (многоголосый)' is a Russian track;
                # 'не требуется' means the original itself is Russian.
                if not re.search(r"(?i)отсутствует|неизвест|без перевода|субтитр|^\s*нет\b|\?", translation[1]):
                    c.claims.append(
                        Claim(field="audio_language", value="rus", evidence_id=evidence.id, quote=line[:500])
                    )
                    line_facts(evidence, line)
                continue
            direct_audio = bool(
                re.match(
                    r"(?i)^\s*(аудио(?:дорожка)?|audio(?: track)?|звук|язык\s+(?:озвучки|аудио))"
                    r"\s*#?\d*\s*(?:[a-zа-я]{2,10}\s*)?[:：]", line
                )
            )
            audio_language = audio_block and bool(re.match(r"(?i)^\s*(language|язык)\s*:", line))
            if not (direct_audio or audio_language):
                continue
            # An audio and subtitle clause can share one line.
            line = re.split(r"(?i)(?:subtitles?|субтитры|text)\s*[:：]?", line, maxsplit=1)[0]
            if re.search(r"(?i)\b(?:not|no|without|нет|без|отсутствует)\b", line):
                continue
            russian = re.search(r"(?i)\b(rus|russian|русский|русская|dub)\b", line)
            if russian:
                c.claims.append(
                    Claim(field="audio_language", value="rus", evidence_id=evidence.id, quote=line[:500])
                )
            if direct_audio:
                for studio, pattern in {**STUDIOS, **extra}.items():
                    if re.search(pattern, line, re.I):
                        c.claims.append(
                            Claim(field="studio", value=studio, evidence_id=evidence.id, quote=line[:500])
                        )
                if russian:
                    for voice in voices_in(line):
                        c.claims.append(
                            Claim(field="voice", value=voice, evidence_id=evidence.id, quote=line[:500])
                        )
    if len(card_codecs) > 1 or (card_codecs and parsed.get("video_codec") not in (None, *card_codecs)):
        c.parsed["video_codec_conflict"] = True
    elif card_codecs and not parsed.get("video_codec"):
        c.parsed["video_codec"] = next(iter(card_codecs))
    apply_resolution_claims(c)
    return c


def apply_resolution_claims(candidate):
    """Fill an unlabeled resolution from one consistent card/LLM resolution claim.

    The title stays authoritative; ffprobe re-checks the file after download.
    """
    values = {cl.value for cl in candidate.claims if cl.field == "resolution"}
    if not candidate.parsed.get("screen_size") and len(values) == 1:
        candidate.parsed["screen_size"] = f"{next(iter(values))}p"


def episode_keys(parsed):
    season = parsed.get("season")
    episodes = parsed.get("episode")
    if not isinstance(season, int) or episodes is None:
        return []
    if isinstance(episodes, int):
        episodes = [episodes]
    return [f"S{season:02d}E{ep:02d}" for ep in episodes if isinstance(ep, int)]
