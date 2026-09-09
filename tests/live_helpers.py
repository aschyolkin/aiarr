import time

import httpx


def wait_http_ready(client, path, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(path, timeout=2)
            if response.status_code == 200:
                return response
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise RuntimeError("Test service did not become ready within its startup deadline")
