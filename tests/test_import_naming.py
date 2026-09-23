from pathlib import Path

import pytest

from mae.importing import destination_for


def test_movie_provider_id_and_container(movie):
    result = destination_for(movie, Path("release.MKV"), Path("/data/store"))
    assert result == Path("/data/store/films/Example (2024) [tmdbid-1]/Example (2024) [tmdbid-1].mkv")


@pytest.mark.parametrize("title", ["../../outside", "a/b\\c", "\x00<script>"])
def test_untrusted_title_stays_in_library(movie, title):
    work = movie.model_copy(update={"title": title})
    result = destination_for(work, Path("release.mkv"), Path("/data/store"))
    assert result.is_relative_to("/data/store/films")
    assert len(result.relative_to("/data/store/films").parts) == 2
    assert "\x00" not in str(result)


@pytest.mark.parametrize(
    "episode", [None, "../../escape", "S01E01+S02E01", "S01E01+S01E03", "S01E02+S01E01", "S01E01+S01E01"]
)
def test_ambiguous_episode_destination_rejected(movie, episode):
    series = movie.model_copy(update={"kind": "series"})
    with pytest.raises(ValueError):
        destination_for(series, Path("release.mkv"), Path("/data/store"), episode)


def test_combined_episode_name_has_exact_contiguous_range(movie):
    series = movie.model_copy(update={"kind": "series"})
    path = destination_for(series, Path("release.mkv"), Path("/data/store"), "S02E01+S02E02")
    assert path.name == "Example - S02E01-E02.mkv"


def test_existing_series_folder_and_season_are_reused(tmp_path, movie):
    root = tmp_path / "store"
    season = root / "serials" / "Silo" / "Season 3"
    season.mkdir(parents=True)
    work = movie.model_copy(update={"kind": "series", "title": "Укрытие", "seasons": [3]})

    result = destination_for(work, Path("release.mkv"), root, "S03E10", season.parent)

    assert result == season / "Silo - S03E10.mkv"


def test_existing_series_folder_must_be_inside_store(tmp_path, movie):
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    work = movie.model_copy(update={"kind": "series"})
    with pytest.raises(ValueError, match="LIBRARY_SERIES_PATH_UNSAFE"):
        destination_for(work, Path("release.mkv"), root, "S01E01", outside)
