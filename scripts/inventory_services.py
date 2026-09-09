"""Run read-only on NAS as administrator. Never outputs credentials.

Uses configured service API keys only locally, does not read external app DBs.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


def get(url, key):
    request = urllib.request.Request(url, headers={"X-Api-Key": key})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    prowlarr_key = ET.parse("/mnt/.ix-apps/app_mounts/prowlarr/config/config.xml").getroot().findtext("ApiKey")
    rows = get("http://127.0.0.1:30050/api/v1/indexer", prowlarr_key)
    print(json.dumps({"prowlarr_indexers": [{"id": row["id"], "name": row["name"],
        "enabled": row.get("enable"), "implementation": row.get("implementation"),
        "synthetic_language_settings": [{"name": f["name"], "value": f.get("value")} for f in row.get("fields", [])
             if f.get("name") in {"addrussian", "addrussiantotitle"}]} for row in rows]}, ensure_ascii=False))
    settings = json.loads(Path("/mnt/.ix-apps/app_mounts/seerr/config/settings.json").read_text())
    key = settings["main"]["apiKey"]
    requests = get("http://127.0.0.1:30357/api/v1/request?take=100", key)
    print(json.dumps({"seerr_requests": len(requests.get("results", [])),
                      "approved_count": sum(r.get("status") == 2 for r in requests.get("results", []))}))
    if "--search" in sys.argv:
        for row in rows:
            query = urllib.parse.urlencode({"apikey": prowlarr_key, "t": "search", "q": "Big Buck Bunny", "limit": 5})
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:30050/{row['id']}/api?{query}", timeout=35) as response:
                    body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    result = {"outcome": "RESPONSE_TOO_LARGE"}
                else:
                    root = ET.fromstring(body)
                    result = {"outcome": "OK" if root.tag == "rss" else "GATEWAY_ERROR", "items": len(root.findall("./channel/item"))}
            except urllib.error.HTTPError as exc:
                result = {"outcome": "HTTP_ERROR", "status": exc.code}
            except Exception as exc:
                result = {"outcome": type(exc).__name__}
            print(json.dumps({"provider": row["name"], **result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
