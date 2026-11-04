# A2 test-only audiovisual fixture

`a2_authoritative_video.mp4` was **not included** in the supplied 66b81092
archive. A number of existing A2 tests require a deterministic, decodable
H.264/AAC clip whose ffprobe duration is 5,042 ms and which contains an audio
stream. Without any fixture those tests error before reaching application code.

For this local Phase 3 regression only, we generated an **entirely synthetic**
80x80 test-pattern film with a 440 Hz synthetic audio tone. This asset is NOT
an Agnes output, NOT an actual source movie, and NOT evidence of production
image/video/audio quality or dialogue synchronization. This fixture only
restores the input assumptions for deterministic ffmpeg/ffprobe code paths.

Reproducible command (ffmpeg 7.1.5):

```bash
ffmpeg -y -f lavfi -i 'testsrc2=size=80x80:rate=24:duration=5.041667' \
  -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=5.041667' \
  -map 0:v:0 -map 1:a:0 -frames:v 121 -c:v libx264 \
  -preset ultrafast -crf 32 -pix_fmt yuv420p -c:a aac -b:a 48k \
  -ac 1 -movflags +faststart -shortest \
  tests/fixtures/a2_authoritative_video.mp4
```

The GitHub-maintained binary fixture, if later supplied, should replace this
synthetic one when validating media fidelity and full audio/video integration.
