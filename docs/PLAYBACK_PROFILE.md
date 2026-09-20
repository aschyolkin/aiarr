# LG G5 profile — not measured / not accepted

## Ready for physical test — 2026-09-20

Production Jellyfin 10.11.11 now contains a separate **AIARR TV Tests** home-video
library with four original synthetic 12-second clips. Exact paths were verified
through the Jellyfin API. No existing movie/episode was modified. Source files:
`/data/store/downloads/films/aiarr-playback-tests-v2` (62,631,258 bytes total).

| Clip | Video / container | Audio | Local checks |
| --- | --- | --- | --- |
| 01 SDR 1080p H264 AAC | H.264 1920x1080 / MP4 | AAC stereo tone | Probe + full decode passed |
| 02 SDR 2160p HEVC AC3 | HEVC Main10 3840x2160 BT.709 / MKV | AC3 stereo tone | Probe + full decode passed |
| 03 HDR10 2160p HEVC AC3 | HEVC Main10 3840x2160 BT.2020/PQ / MKV | AC3 stereo tone | Probe + full decode passed |
| 04 HDR10 2160p HEVC AC3 | Same encoded HDR10 streams / MP4 | AC3 stereo tone | Probe + full decode passed |

HDR generation transforms the BT.709 test pattern to BT.2020/PQ using zscale;
it is not merely an HDR tag on an unchanged SDR stream. This is a signaling and
compatibility test, not calibrated display measurement. Audio is a quiet 440 Hz
tone tagged `und`, NOT Russian speech, surround-channel validation or Atmos.
Dolby Vision is not covered. Short clips do not measure sustained NAS-to-TV
throughput or worst-case movie bitrate; no optimized bitrate limit is claimed.

On LG, open the normal Jellyfin webOS app and the **AIARR TV Tests** library.
Play clips 01 through 04; repeat if needed to capture the short session. Record
webOS/app versions, Ethernet/Wi-Fi and TV-speaker/eARC receiver path. Confirm
picture, smooth playback and audible tone for each clip, then HDR indication
and sensible colors for 03/04. Do not claim Dolby Vision based on HDR playback.
Keep subtitles off. Record the current MKV-to-MP4 remux setting; if MKV HDR fails,
test that setting separately, as described in the official Jellyfin docs linked
below. Do not silently change server-wide transcoding settings.

While a clip is playing, capture read-only server evidence:
`docker exec ix-aiarr-worker-1 python -m mae.playback` on the NAS.
The report distinguishes direct play, pure remux, audio transcode, video transcode
and unknown/inconsistent telemetry. It excludes user names, file paths, device
IDs and session tokens. An empty session list is not a successful playback test.
API telemetry cannot certify the display's HDR mode, audible output or colors.

Reproducible generator: `tests/prepare_tv_fixtures.py --prepare`, in a disposable
container using the aiarr image, Python entrypoint, >=3 GiB memory and two CPUs,
with only the dedicated fixture directory writable and privately supplied
Jellyfin URL/key. Do not run 4K generation in the 1 GiB production worker.
Existing completed fixtures are checksum-verified; unrelated/partial files are
not overwritten. First failed v1 generation remains unregistered for inspection;
no user data was deleted. The v2 one-shot container exited successfully and was
automatically removed.

Status: **READY_FOR_TV**, not accepted playback. All observations below still
require real device evidence.

Base selection: WEB/Blu-ray equal weights, 2160p preferred, 1080p allowed,
H.265 preferred over H.264, max release 60 GiB, Russian audio required,
all studio weights 0, upgrades disabled. This is a starting policy, NOT an
optimized LG/device/channel profile. Original audio preference and per-episode
size budget still need implementation.

[Jellyfin codec documentation](https://jellyfin.org/docs/general/clients/codec-support/)
must be compared with actual webOS/client version, container, HDR/DV profile and
audio path. Do not infer Direct Play from H.265 support alone.

Required measurements: Internet download throughput; NAS-to-TV throughput;
Jellyfin playback session's DirectPlay/DirectStream/Transcode and transcode reason;
HDR/DV indicator and visible colors; actual audible channel output with TV/eARC
receiver settings. Test MKV/MP4, SDR/HDR10/DV and supported audio formats separately.
Record averages and peaks separately. Require user observation for picture/sound.

ffprobe evidence is container/stream metadata, not speech recognition, actual
studio identification, complete decoding, or confirmation of television playback.
No television observations or network measurements have been performed in this task.
