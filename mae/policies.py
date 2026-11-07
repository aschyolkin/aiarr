import re

from mae.parsing import episode_keys, normalize_title


def _words(value):
    return re.findall(r"\w+", value.casefold())


def title_matches(title, work, year, release_names=()):
    """Exact normalized match of the parsed title or any 'Рус / Eng' name part,
    or for a movie of the right year every word of a title/alias of at least
    three words appearing in one of them."""
    names = [work.title, *work.aliases]
    wanted = {normalize_title(t) for t in names}
    candidates = [t for t in (title, *release_names) if isinstance(t, str) and t]
    if any(normalize_title(t) in wanted for t in candidates):
        return True
    if work.kind != "movie" or work.year is None or year != work.year:
        return False
    return any(
        len(_words(name)) >= 3 and set(_words(name)) <= set(_words(t)) for name in names for t in candidates
    )


def evaluate(candidate, work, policy):
    c = candidate.model_copy(deep=True)
    c.reasons, c.unknown, c.score = [], [], {}
    p = c.parsed
    title = p.get("title", "")
    if not title_matches(title if isinstance(title, str) else "", work, p.get("year"), p.get("names") or ()):
        c.reasons.append("IDENTITY_MISMATCH")
    if p.get("not_video"):
        c.reasons.append("NOT_VIDEO_RELEASE")
    if work.kind == "movie":
        if p.get("year") != work.year or work.year is None:
            c.reasons.append("YEAR_UNVERIFIED")
        if p.get("episode") is not None:
            c.reasons.append("NOT_A_MOVIE")
    else:
        if p.get("season") not in work.seasons:
            c.reasons.append("SEASON_MISMATCH")
        keys = episode_keys(p)
        if keys and (
            not set(keys).issubset(set(work.episodes) | set(work.covered_episodes))
            or not set(keys).intersection(work.episodes)
        ):
            c.reasons.append("EPISODE_MISMATCH")
        if not work.episodes:
            c.unknown.append("desired_episode_coverage")
    source = p.get("source")
    if source in policy.denied_sources:
        c.reasons.append("SOURCE_NOT_ALLOWED")
    resolution = str(p.get("screen_size", ""))
    if resolution not in [f"{n}p" for n in [*policy.resolutions, *policy.fallback_resolutions]]:
        c.reasons.append("RESOLUTION_NOT_ALLOWED")
    # An unlabeled codec is left to ffprobe verification after download.
    if p.get("video_codec") is not None and p.get("video_codec") not in policy.codecs:
        c.reasons.append("VIDEO_CODEC_NOT_ALLOWED")
    if p.get("video_codec_conflict"):
        c.reasons.append("CODEC_EVIDENCE_CONFLICT")
    if c.release.size is None:
        c.unknown.append("size")
    elif c.release.size > policy.max_bytes:
        c.reasons.append("TOO_LARGE")
    elif work.kind == "movie" and c.release.size > policy.max_file_bytes:
        # A movie release is essentially its one video file.
        c.reasons.append("FILE_TOO_LARGE")
    if policy.russian_required and not any(
        cl.field == "audio_language" and cl.value == "rus" for cl in c.claims
    ):
        c.unknown.append("russian_audio")
    if c.reasons or c.unknown:
        return c
    c.score = {
        "source": policy.source_weights.get(source, 0),
        "resolution": 30 if resolution == "2160p" else 10 if resolution == "1080p" else 0,
        "codec": 10 if p.get("video_codec") == "H.265" else 0,
    }
    studios = {cl.value for cl in c.claims if cl.field == "studio"}
    c.score["studio"] = max([policy.studio_weights.get(s, 0) for s in studios], default=0)
    voices = {cl.value for cl in c.claims if cl.field == "voice"}
    c.score["voice"] = max([policy.voice_weights.get(v, 0) for v in voices], default=0)
    return c
