from mae.parsing import episode_keys, normalize_title


def evaluate(candidate, work, policy):
    c = candidate.model_copy(deep=True)
    c.reasons, c.unknown, c.score = [], [], {}
    p = c.parsed
    title = p.get("title", "")
    aliases = {normalize_title(t) for t in [work.title, *work.aliases]}
    if not isinstance(title, str) or normalize_title(title) not in aliases:
        c.reasons.append("IDENTITY_MISMATCH")
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
    if resolution not in [f"{n}p" for n in policy.resolutions]:
        c.reasons.append("RESOLUTION_NOT_ALLOWED")
    if p.get("video_codec") not in policy.codecs:
        c.unknown.append("compatible_video_codec")
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
        "resolution": 30 if resolution == "2160p" else 10,
        "codec": 10 if p.get("video_codec") == "H.265" else 0,
    }
    studios = {cl.value for cl in c.claims if cl.field == "studio"}
    c.score["studio"] = max([policy.studio_weights.get(s, 0) for s in studios], default=0)
    return c
