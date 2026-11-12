import re

from mae.verification import check_path


def destination_for(work, source, store, episode=None, series_folder=None):
    title = re.sub(r"[^\w .-]", "_", work.title)[:100].strip(" .") or "Untitled"
    year = f" ({work.year})" if work.year else ""
    folder_name = f"{title}{year} [{work.library_namespace}-{work.library_id}]"
    folder = store / ("films" if work.kind == "movie" else "serials") / folder_name
    if work.kind == "series":
        if series_folder is not None:
            if series_folder.parent != store / "serials" or not series_folder.is_dir():
                raise ValueError("LIBRARY_SERIES_PATH_UNSAFE")
            check_path(series_folder, store)
            folder = series_folder
            title = re.sub(r"[^\w .-]", "_", folder.name)[:100].strip(" .") or "Untitled"
        if not episode or not re.fullmatch(r"S\d{2,3}E\d{2,3}(?:\+S\d{2,3}E\d{2,3})*", episode):
            raise ValueError("UNVERIFIED_EPISODE_DESTINATION")
        seasons = {key.split("E")[0] for key in episode.split("+")}
        if len(seasons) != 1:
            raise ValueError("CROSS_SEASON_COMBINED_FILE")
        number = int(next(iter(seasons))[1:])
        season_names = [f"Season {number}", f"Season {number:02d}"]
        existing = [folder / name for name in dict.fromkeys(season_names) if (folder / name).exists()]
        if len(existing) > 1:
            raise ValueError("AMBIGUOUS_SEASON_FOLDER")
        folder = existing[0] if existing else folder / season_names[-1]
        keys = episode.split("+")
        numbers = [int(key.split("E")[1]) for key in keys]
        if numbers != list(range(numbers[0], numbers[-1] + 1)):
            raise ValueError("NONCONTIGUOUS_COMBINED_EPISODES")
        label = keys[0] + (f"-E{numbers[-1]:02d}" if len(keys) > 1 else "")
        name = f"{title} - {label}"
    else:
        name = folder_name
    return folder / (name + source.suffix.lower())
