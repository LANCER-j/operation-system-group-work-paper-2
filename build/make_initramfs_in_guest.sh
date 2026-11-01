#!/usr/bin/env bash
# 此脚本只在可丢弃的 Ubuntu 18.04 实验虚拟机内运行。
# 它写入该虚拟机 /lib/modules 和 /boot，但不修改引导器。
set -Eeuo pipefail
export LC_ALL=C
umask 022

usage() {
    echo '用法（仅实验虚拟机内）：sudo bash make_initramfs_in_guest.sh cfs|ule /path/to/artifact-directory'
}
[[ $# == 2 ]] || { usage >&2; exit 2; }
variant=$1
case "$variant" in cfs|ule) ;; *) usage >&2; exit 2 ;; esac
[[ $(id -u) == 0 ]] || { echo '需要在实验虚拟机内使用 sudo。' >&2; exit 1; }
command -v systemd-detect-virt >/dev/null
systemd-detect-virt --vm >/dev/null || {
    echo '未检测到虚拟机，拒绝写入 /boot 和 /lib/modules。' >&2
    exit 1
}
for executable in sha256sum tar install depmod mkinitramfs; do
    command -v "$executable" >/dev/null || { echo "缺少 $executable" >&2; exit 1; }
done
artifact=$(cd -- "$2" && pwd -P)
release=$(cat "$artifact/kernelrelease.txt")
[[ "$release" == "4.9.0-paper02-$variant" ]] || { echo '内核版本与变体不匹配。' >&2; exit 1; }
(
    cd -- "$artifact"
    sha256sum --check SHA256SUMS
)
tar --no-same-owner --no-same-permissions -xzf "$artifact/modules-$release.tar.gz" -C /
install -m 0644 "$artifact/config" "/boot/config-$release"
install -m 0644 "$artifact/System.map" "/boot/System.map-$release"
depmod -a "$release"
# 直接生成产物，避免 update-initramfs 的引导器更新钩子。
mkinitramfs -o "$artifact/initrd.img" "$release"
(
    cd -- "$artifact"
    sha256sum initrd.img > INITRD_SHA256SUMS
)
printf '已生成：%s/initrd.img。尚未证明该内核能够启动。\n' "$artifact"
