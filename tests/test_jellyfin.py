import httpx

from mae.domain import Work
from mae.jellyfin import Jellyfin


def test_unrelated_result_is_not_available(settings, movie, respx_mock):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key})
    route = respx_mock.get(settings.jellyfin_url + "/Items")
    route.mock(
        return_value=httpx.Response(
            200, json={"Items": [{"ProviderIds": {"Tmdb": "99"}, "Path": "/some/movie.mkv"}]}
        )
    )
    assert Jellyfin(settings).coverage(movie) == set()
    route.mock(
        return_value=httpx.Response(
            200, json={"Items": [{"ProviderIds": {"Tmdb": "1"}, "Path": "/some/movie.mkv"}]}
        )
    )
    assert Jellyfin(settings).coverage(movie) == {"movie"}


def test_partial_and_combined_episode_coverage(settings, respx_mock):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key})
    work = Work(
        kind="series", external_id=7, title="Test", seasons=[1], episodes=["S01E01", "S01E02", "S01E03"]
    )
    respx_mock.get(settings.jellyfin_url + "/Items").mock(
        return_value=httpx.Response(200, json={"Items": [{"Id": "s", "ProviderIds": {"Tvdb": "7"}}]})
    )
    respx_mock.get(settings.jellyfin_url + "/Shows/s/Episodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "Items": [
                    {"Path": "/file.mkv", "ParentIndexNumber": 1, "IndexNumber": 1, "IndexNumberEnd": 2},
                    {"ParentIndexNumber": 1, "IndexNumber": 3, "IsMissing": True},
                ]
            },
        )
    )
    assert Jellyfin(settings).coverage(work) == {"S01E01", "S01E02"}
