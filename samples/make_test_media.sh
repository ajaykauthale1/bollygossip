#!/usr/bin/env bash
# Generates the dummy clips and music used by samples/sample_posts.json (offline test run).
set -euo pipefail
cd "$(dirname "$0")"
FF=$(command -v ffmpeg || python3 -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
"$FF" -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=440 -t 6 -c:v libx264 -pix_fmt yuv420p -c:a aac test_landscape.mp4
"$FF" -loglevel error -y -f lavfi -i testsrc=size=720x1280:rate=24 -t 5 -c:v libx264 -pix_fmt yuv420p test_vertical_silent.mp4
"$FF" -loglevel error -y -f lavfi -i "sine=frequency=220,volume=0.5" -t 9 test_music.mp3
echo "Test media ready."
