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
