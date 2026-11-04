#!/usr/bin/env bash
# CI media tools, without apt locks or third-party package repository refreshes.
set -euo pipefail

if [[ "${FFMPEG_BOOTSTRAP_FORCE:-0}" != 1 ]] && command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then
  ffmpeg -version | sed -n '1p'
  ffprobe -version | sed -n '1p'
  exit 0
fi

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'The pinned media bootstrap requires Linux x86_64.' >&2
  exit 1
fi
: "${RUNNER_TEMP:?RUNNER_TEMP is required for CI media installation}"
: "${GITHUB_PATH:?GITHUB_PATH is required to expose the media tools}"

# A month-end release is retained for two years; daily builds expire in 14 days.
# Keep the immutable filename and GitHub release SHA256 together when updating.
release=autobuild-2026-09-30-13-08
package=ffmpeg-n8.1.3-9-g29e619e767-linux64-gpl-8.1
checksum=97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79
work=$(mktemp -d "$RUNNER_TEMP/ffmpeg-download.XXXXXX")
trap 'rm -rf "$work"' EXIT
archive="$work/media.tar.xz"
bin_dir="$RUNNER_TEMP/collab-ffmpeg/bin"

echo "Fetching pinned media tools: $release / $package"
curl --fail --location --show-error --silent \
  --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 2 \
  --output "$archive" \
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/$release/$package.tar.xz"
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --strict
mkdir -p "$bin_dir"
# Extract only the two named regular binaries from the verified archive.
tar -xJf "$archive" --strip-components=2 -C "$bin_dir" \
  "$package/bin/ffmpeg" "$package/bin/ffprobe"
chmod 755 "$bin_dir/ffmpeg" "$bin_dir/ffprobe"
"$bin_dir/ffmpeg" -version | sed -n '1p'
"$bin_dir/ffprobe" -version | sed -n '1p'
printf '%s\n' "$bin_dir" >> "$GITHUB_PATH"
