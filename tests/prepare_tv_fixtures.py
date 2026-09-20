"""Opt-in synthetic LG playback fixtures. No tracker content or existing-file edits.

Run in a disposable container with >=3 GiB RAM, not the production worker.
Use --prepare. Creates a separate Jellyfin home-video
library. Requires ffmpeg libx264/libx265/zscale. Does not certify display behavior.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import time

from mae.config import Settings
from mae.jellyfin import Jellyfin

LIBRARY = "AIARR TV Tests"
DIRECTORY = "aiarr-playback-tests-v2"
FILES = (
    "01 SDR 1080p H264 AAC.mp4",
    "02 SDR 2160p HEVC AC3.mkv",
    "03 HDR10 2160p HEVC AC3.mkv",
    "04 HDR10 2160p HEVC AC3.mp4",
)


def encode_command(target, *, uhd=False, hdr=False):
    size = "3840x2160" if uhd else "1920x1080"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
           "-t", "12", "-map", "0:v:0", "-map", "1:a:0", "-filter_threads", "2",
           "-c:v", "libx265" if uhd else "libx264", "-threads", "2", "-preset", "ultrafast",
           "-crf", "24", "-af", "volume=0.12", "-ac", "2",
           "-c:a", "ac3" if uhd else "aac", "-b:a", "192k",
           "-metadata", "comment=Original synthetic aiarr fixture; tone, not speech; no Dolby Vision",
           "-metadata:s:a:0", "language=und"]
    if uhd:
        params = "pools=2:frame-threads=2:log-level=error"
        if hdr:
            cmd += ["-vf", "zscale=pin=bt709:tin=bt709:min=bt709:rin=limited:"
                    "p=bt2020:t=smpte2084:m=bt2020nc:r=limited:npl=100,format=yuv420p10le",
                    "-color_primaries", "bt2020", "-color_trc", "smpte2084",
                    "-colorspace", "bt2020nc"]
            params += (":hdr10=1:repeat-headers=1:colorprim=9:transfer=16:colormatrix=9:"
                       "master-display=G(13250,34500)B(7500,3000)R(34000,16000)"
                       "WP(15635,16450)L(10000000,1):max-cll=1000,400")
        else:
            cmd += ["-pix_fmt", "yuv420p10le", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-colorspace", "bt709"]
        cmd += ["-x265-params", params]
    else:
        cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    return cmd + [str(target)]


def run(cmd):
    # No shell expansion; bounded runtime and output, no dependency on host ffmpeg.
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError("FIXTURE_COMMAND_FAILED")
    return result.stdout


def prepare(settings):
    root = settings.store / "downloads" / "films" / DIRECTORY
    marker = root / "aiarr-fixtures.json"
    if root.is_symlink() or not root.resolve().is_relative_to(settings.store.resolve()):
        raise ValueError("UNSAFE_FIXTURE_DIRECTORY")
    if root.exists() and any(root.iterdir()):
        # Never overwrite files from an earlier run or an unrelated directory.
        if not marker.is_file():
            raise ValueError("FIXTURE_DIRECTORY_EXISTS_WITHOUT_MANIFEST")
        manifest = json.loads(marker.read_text())
        if manifest.get("generator") != DIRECTORY or set(manifest.get("files", {})) != set(FILES):
            raise ValueError("UNRECOGNIZED_FIXTURE_MANIFEST")
        for name, digest in manifest["files"].items():
            path = root / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("FIXTURE_CHECKSUM_MISMATCH")
    else:
        if shutil.disk_usage(root.parent).free < 2 * 1024**3:
            raise ValueError("INSUFFICIENT_FIXTURE_SPACE")
        root.mkdir(mode=0o755, exist_ok=True)
        for index, flags in enumerate(({}, {"uhd": True}, {"uhd": True, "hdr": True})):
            run(encode_command(root / FILES[index], **flags))
            print(json.dumps({"generated": FILES[index]}), flush=True)
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
             "-i", str(root / FILES[2]), "-map", "0", "-c", "copy", "-tag:v", "hvc1",
             "-movflags", "+faststart", str(root / FILES[3])])
        manifest = {"generator": DIRECTORY, "files": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in FILES}}
        marker.write_text(json.dumps(manifest, indent=2) + "\n")
    probes = []
    for index, name in enumerate(FILES):
        path = root / name
        result = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                 "-of", "json", str(path)]))
        video = next(s for s in result["streams"] if s["codec_type"] == "video")
        audio = next(s for s in result["streams"] if s["codec_type"] == "audio")
        assert video["width"] == (1920 if index == 0 else 3840)
        assert video["codec_name"] == ("h264" if index == 0 else "hevc")
        assert audio["codec_name"] == ("aac" if index == 0 else "ac3")
        assert 11 <= float(result["format"]["duration"]) <= 13
        if index >= 2:
            assert video["color_transfer"] == "smpte2084"
            assert video["color_primaries"] == "bt2020"
            assert video["pix_fmt"] == "yuv420p10le"
        # Probe alone is insufficient: decode every frame of these short fixtures.
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin",
             "-threads", "2", "-i", str(path), "-map", "0:v", "-map", "0:a", "-f", "null", "-"])
        probes.append({"name": name, "bytes": path.stat().st_size, "width": video["width"],
                       "codec": video["codec_name"], "transfer": video.get("color_transfer"),
                       "audio": audio["codec_name"], "full_decode": True})
    with Jellyfin(settings).client() as client:
        folders = client.get("/Library/VirtualFolders")
        folders.raise_for_status()
        existing = [f for f in folders.json() if f.get("Name") == LIBRARY]
        if existing:
            if len(existing) != 1 or existing[0].get("Locations") != [str(root)]:
                raise ValueError("LIBRARY_NAME_COLLISION")
        else:
            response = client.post("/Library/VirtualFolders", params={
                "name": LIBRARY, "collectionType": "homevideos", "paths": str(root),
                "refreshLibrary": "true",
            }, json={"LibraryOptions": {
                "EnableRealtimeMonitor": False,
                "TypeOptions": [{"Type": kind, "MetadataFetchers": [], "ImageFetchers": []}
                                for kind in ("Movie", "Video")],
            }})
            response.raise_for_status()
        # Verify discovery by exact paths; API success alone is not acceptance.
        for _ in range(30):
            response = client.get("/Items", params={"Recursive": "true", "Fields": "Path"})
            response.raise_for_status()
            paths = {i.get("Path") for i in response.json().get("Items", [])}
            if all(str(root / name) in paths for name in FILES):
                print(json.dumps({"result": "READY_FOR_TV", "library": LIBRARY, "files": probes,
                                  "tv_observation": "NOT_PERFORMED", "dolby_vision": "NOT_TESTED"}))
                return
            time.sleep(2)
        raise RuntimeError("FIXTURE_DISCOVERY_TIMEOUT")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.parse_args()
    try:
        prepare(Settings())
    except Exception as exc:
        print(json.dumps({"result": "FAIL", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
