"""Opt-in public Rutor card/files probe. No torrent payload or download client."""

import json

from mae.config import ProviderConfig
from mae.domain import ProviderError, SearchResult
from mae.enrichment import RutorDetailsEnricher
from mae.parsing import parse_candidate


def main():
    config = ProviderConfig(
        name="rutor",
        url="https://rutor.info",
        source_host="rutor.info",
        details_adapter="rutor",
        trusted_source=True,
    )
    release = SearchResult(
        provider="rutor",
        release_id="384544",
        title="Big Buck Bunny",
        details_url="https://rutor.info/torrent/384544/bolshoj-bak_big-buck-bunny-2008-bdrip-1080p-ot-ash61-3d-video-halfou",
    )
    enricher = RutorDetailsEnricher(config)
    try:
        details = enricher.fetch_details(release)
        files = enricher.fetch_file_list(release)
    except ProviderError as error:
        print(
            json.dumps(
                {
                    "result": "BLOCKED",
                    "source": "rutor.info",
                    "outcome": error.outcome.value,
                    "network": "container source network cannot reach configured source",
                }
            ),
            flush=True,
        )
        return 2
    assert len(files) == 1 and files[0].endswith(".mkv")
    assert "MediaInfo" in details.evidence[0].text and "Видео" in details.evidence[0].text
    candidate = parse_candidate(release, details)
    assert not any(c.field == "audio_language" and c.value == "rus" for c in candidate.claims)
    print(
        json.dumps(
            {
                "result": "PASS",
                "source": "rutor.info",
                "topic": "384544",
                "capabilities_tested": ["public_card", "technical_evidence", "flat_file_list"],
                "files": len(files),
                "russian_audio_claim": False,
                "download": "not attempted",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main() or 0)
