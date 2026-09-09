from unittest.mock import Mock

import pytest

from mae.config import ProviderConfig, Settings
from mae.domain import Outcome, ProviderError, SearchResult
from mae.enrichment import RutorDetailsEnricher

CARD = """<table id="details"><tr><td></td><td><b>Аудио:</b> русский
<div class="hidebody">Audio<br>Language: Russian</div></td></tr>
<tr><td>Comment</td><td>Unrelated user comment</td></tr></table>
<script>$('#filelist').load('/descriptions/42.files');</script>"""


def adapter(files):
    config = ProviderConfig(
        name="rutor",
        url="https://gateway.example",
        source_host="rutor.info",
        details_adapter="rutor",
        interval=0,
    )
    # Pydantic nested validation must accept an already constructed config.
    Settings(api_key="offline-rutor-key-000000000000", providers=[config], _env_file=None)
    http = Mock()
    http.get.side_effect = [CARD.encode(), files]
    release = SearchResult(
        provider="rutor",
        release_id="42",
        title="Synthetic",
        details_url="https://rutor.info/torrent/42/synthetic",
    )
    return RutorDetailsEnricher(config, http), http, release


def test_card_is_separate_from_comments_and_ajax_file_list():
    enricher, http, release = adapter(b"<tr><td>Show/S01E01.mkv</td><td>1 MB</td></tr>")
    details = enricher.fetch_details(release)
    assert "русский" in details.evidence[0].text
    assert "Unrelated user comment" not in details.evidence[0].text
    assert enricher.fetch_file_list(release) == ["Show/S01E01.mkv"]
    assert http.get.call_args.args[0] == "https://rutor.info/descriptions/42.files"
    assert http.get.call_count == 2


@pytest.mark.parametrize(
    "files,outcome",
    [
        (b"<tr><td>../bad.mkv</td><td>1 MB</td></tr>", Outcome.PARSER_BROKEN),
        (b"<tr><td>/absolute.mkv</td><td>1 MB</td></tr>", Outcome.PARSER_BROKEN),
        (b"<tr><td><span>folder</span></td><td></td></tr>", Outcome.UNSUPPORTED_CAPABILITY),
        (b"<form><input type='password'></form>", Outcome.AUTH_REQUIRED),
        (b"Changed layout", Outcome.PARSER_BROKEN),
    ],
)
def test_unverified_file_tree_is_not_a_list_of_safe_files(files, outcome):
    enricher, _, release = adapter(files)
    with pytest.raises(ProviderError) as error:
        enricher.fetch_file_list(release)
    assert error.value.outcome == outcome
