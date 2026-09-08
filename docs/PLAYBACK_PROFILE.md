# LG G5 profile — not measured / not accepted

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
