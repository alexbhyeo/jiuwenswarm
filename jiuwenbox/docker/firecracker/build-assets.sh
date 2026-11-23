#!/usr/bin/env bash
# Builds the golden Firecracker guest assets (kernel + rootfs) for the
# jiuwenbox-firecracker image: fetches the latest Firecracker CI kernel +
# Ubuntu squashfs rootfs (same source as firecracker/docs/getting-started.md),
# bakes in guest_agent.py as a systemd service, and repacks it as ext4.
#
# Runs inside the "fc-assets" build stage (see ../Dockerfile.firecracker).
set -euo pipefail

OUT_DIR="${1:?usage: build-assets.sh <output-dir>}"
ASSETS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

ARCH="$(uname -m)"
S3="https://s3.amazonaws.com/spec.ccfc.min"

CI_ARTIFACTS_PREFIX=$(curl -fsSL "$S3?list-type=2&prefix=firecracker-ci/&delimiter=/" \
    | grep -oP "(?<=<Prefix>)firecracker-ci/[0-9]{8}-[^/]+/(?=</Prefix>)" \
    | sort | tail -1)

latest_kernel_key=$(curl -fsSL "$S3?list-type=2&prefix=${CI_ARTIFACTS_PREFIX}${ARCH}/vmlinux-" \
    | grep -oP "(?<=<Key>)(${CI_ARTIFACTS_PREFIX}${ARCH}/vmlinux-[0-9]+\.[0-9]+\.[0-9]{1,3})(?=</Key>)" \
    | sort -V | tail -1)
wget -q "$S3/${latest_kernel_key}" -O vmlinux

latest_ubuntu_key=$(curl -fsSL "$S3?list-type=2&prefix=${CI_ARTIFACTS_PREFIX}${ARCH}/ubuntu-" \
    | grep -oP "(?<=<Key>)(${CI_ARTIFACTS_PREFIX}${ARCH}/ubuntu-[0-9]+\.[0-9]+\.squashfs)(?=</Key>)" \
    | sort -V | tail -1)
wget -q "$S3/${latest_ubuntu_key}" -O ubuntu.squashfs.upstream

unsquashfs -d squashfs-root ubuntu.squashfs.upstream

# Bake in the guest-agent: it replaces SSH as the host<->guest control
# channel for this design (vsock, no network setup needed), so no SSH key
# injection is needed here.
install -m 0755 "$ASSETS_DIR/guest_agent.py" squashfs-root/usr/local/bin/guest_agent.py
install -m 0644 "$ASSETS_DIR/guest-agent.service" squashfs-root/etc/systemd/system/guest-agent.service
mkdir -p squashfs-root/etc/systemd/system/multi-user.target.wants
ln -sf ../guest-agent.service squashfs-root/etc/systemd/system/multi-user.target.wants/guest-agent.service

chown -R root:root squashfs-root
truncate -s 1G rootfs.ext4
mkfs.ext4 -q -d squashfs-root -F rootfs.ext4

rm -rf squashfs-root ubuntu.squashfs.upstream
echo "built: $OUT_DIR/vmlinux $OUT_DIR/rootfs.ext4"
