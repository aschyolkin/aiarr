import re


def destination_for(work, source, store, episode=None):
    title = re.sub(r"[^\w .-]", "_", work.title)[:100].strip(" .") or "Untitled"
    namespace = "tmdbid" if work.kind == "movie" else "tvdbid"
    year = f" ({work.year})" if work.year else ""
    folder_name = f"{title}{year} [{namespace}-{work.external_id}]"
    folder = store / ("films" if work.kind == "movie" else "serials") / folder_name
    if work.kind == "series":
        if not episode or not re.fullmatch(r"S\d{2,3}E\d{2,3}(?:\+S\d{2,3}E\d{2,3})*", episode):
            raise ValueError("UNVERIFIED_EPISODE_DESTINATION")
        seasons = {key.split("E")[0] for key in episode.split("+")}
        if len(seasons) != 1:
            raise ValueError("CROSS_SEASON_COMBINED_FILE")
        folder = folder / f"Season {int(next(iter(seasons))[1:]):02d}"
        name = f"{title} - {episode}"
    else:
        name = folder_name
    return folder / (name + source.suffix.lower())
