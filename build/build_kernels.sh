#!/usr/bin/env bash
# 仅在工作目录内下载、配置和构建；不执行 make install，不修改 GRUB。
set -Eeuo pipefail
export LC_ALL=C
umask 022

readonly CFS_SHA=69973b830859bc6529a7a0468ba0d80ee5117826
readonly ULE_SHA=8e76f4826daadebce6e7f5fe9cbd48823fd403a0
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
    cat <<'EOF'
用法：bash build_kernels.sh [all|cfs|ule|configure]
  all        配置并构建两个内核（默认）
  cfs / ule  仍验证两份配置一致，但只构建指定内核
  configure  下载源码并生成、比较配置，不编译内核

环境变量：
  PAPER02_BUILD_ROOT  所有下载、源码、日志和输出的位置，默认 build/work
  JOBS               并行编译数，默认 2
  CC                 默认 gcc-7；HOSTCC 默认与 CC 相同

目标环境：Ubuntu 18.04 x86_64，GCC 7；本周提交为实验启动阶段代码。
脚本不会安装依赖、安装宿主内核、写 /boot 或修改引导配置。
EOF
}

mode=${1:-all}
case "$mode" in
    -h|--help) usage; exit 0 ;;
    all|cfs|ule|configure) ;;
    *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
    printf '%s\n' '需要 Linux x86_64 构建环境；不能在 PowerShell 或 Git Bash 直接构建。' >&2
    exit 1
}

CC=${CC:-gcc-7}
HOSTCC=${HOSTCC:-$CC}
JOBS=${JOBS:-2}
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS 必须为正整数。' >&2; exit 2; }

for executable in python3 cmp "$CC" "$HOSTCC" make ld as ar nm objcopy objdump perl bc curl tar gzip xz sha256sum awk sed grep sort diff tee mktemp; do
    command -v "$executable" >/dev/null 2>&1 || {
        printf '缺少构建工具：%s；请先查看 README。\n' "$executable" >&2
        exit 1
    }
done

requested_root=${PAPER02_BUILD_ROOT:-"$SCRIPT_DIR/work"}
mkdir -p -- "$requested_root"
BUILD_ROOT=$(cd -- "$requested_root" && pwd -P)
[[ "$BUILD_ROOT" != / ]] || { echo '构建目录不能为 /。' >&2; exit 2; }
[[ "$BUILD_ROOT" != *[[:space:]]* ]] || { echo '旧内核构建目录不能包含空白字符。' >&2; exit 2; }
mkdir -p -- "$BUILD_ROOT/downloads" "$BUILD_ROOT/sources" "$BUILD_ROOT/objects" \
    "$BUILD_ROOT/logs" "$BUILD_ROOT/artifacts" "$BUILD_ROOT/modules-stage"

trap 'printf "构建失败（行 %s），请保留 %s/logs 和源码用于排查。\n" "$LINENO" "$BUILD_ROOT" >&2' ERR

{
    printf 'created_utc=%s\n' "$(date -u +%FT%TZ)"
    printf 'cfs_sha=%s\nule_sha=%s\n' "$CFS_SHA" "$ULE_SHA"
    printf 'cc=%s\nhostcc=%s\njobs=%s\n' "$CC" "$HOSTCC" "$JOBS"
    printf 'build_host='; uname -a
    "$CC" --version
    "$HOSTCC" --version
    ld --version
    make --version
    [[ ! -r /etc/os-release ]] || cat /etc/os-release
} > "$BUILD_ROOT/logs/toolchain.txt"

download_source() {
    local variant=$1 sha=$2
    local archive="$BUILD_ROOT/downloads/linux-$sha.tar.gz"
    local source="$BUILD_ROOT/sources/$variant-$sha"
    local temporary_source
    # 支持由官方归档与已核验作者补丁预置的源码；优先检查它，避免再次下载。
    if [[ -d "$source" ]]; then
        [[ -r "$source/.paper02-source-sha" && "$(cat "$source/.paper02-source-sha")" == "$sha" ]] || {
            printf '预置源码来源标记不匹配：%s\n' "$source" >&2
            return 1
        }
        [[ -f "$source/kernel/configs/kvm_guest.config" && -f "$source/Makefile" ]] || {
            printf '预置源码缺少 Linux 4.9 构建文件：%s\n' "$source" >&2
            return 1
        }
        {
            printf 'source_mode=preexisting_tree\nsource_path=%s\nsource_sha_marker=%s\n' "$source" "$sha"
            printf 'download_skipped=true\nmarker_is_not_content_verification=true\n'
            sha256sum "$source/Makefile" "$source/kernel/configs/kvm_guest.config"
        } > "$BUILD_ROOT/logs/source-$variant.txt"
        return
    fi
    if [[ ! -f "$archive" ]]; then
        curl --fail --location --retry 3 --connect-timeout 20 --max-time 3600 \
            "https://codeload.github.com/JBouron/linux/tar.gz/$sha" \
            --output "$archive.part"
        tar -tzf "$archive.part" >/dev/null
        mv -- "$archive.part" "$archive"
    fi
    sha256sum "$archive" > "$BUILD_ROOT/logs/download-$variant.sha256"
    if [[ ! -d "$source" ]]; then
        temporary_source=$(mktemp -d "$BUILD_ROOT/sources/.extract-$variant.XXXXXX")
        tar --no-same-owner --no-same-permissions -xzf "$archive" \
            -C "$temporary_source" --strip-components=1
        printf '%s\n' "$sha" > "$temporary_source/.paper02-source-sha"
        mv -- "$temporary_source" "$source"
    fi
    [[ -r "$source/.paper02-source-sha" && "$(cat "$source/.paper02-source-sha")" == "$sha" ]] || {
        printf '源码来源标记不匹配：%s\n' "$source" >&2
        return 1
    }
    [[ -f "$source/kernel/configs/kvm_guest.config" ]] || {
        echo '预期的 Linux 4.9 kvm_guest.config 不存在。' >&2
        return 1
    }
    printf 'source_mode=downloaded_archive\nsource_sha_marker=%s\n' "$sha" \
        > "$BUILD_ROOT/logs/source-$variant.txt"
}

kmake() {
    local source=$1 object=$2
    shift 2
    make -C "$source" O="$object" ARCH=x86_64 CC="$CC" HOSTCC="$HOSTCC" \
        LOCALVERSION= KBUILD_BUILD_USER=paper02 KBUILD_BUILD_HOST=replication \
        KBUILD_BUILD_TIMESTAMP='2018-05-02 15:39:10 UTC' "$@"
}

download_source cfs "$CFS_SHA"
download_source ule "$ULE_SHA"
CFS_SOURCE="$BUILD_ROOT/sources/cfs-$CFS_SHA"
ULE_SOURCE="$BUILD_ROOT/sources/ule-$ULE_SHA"
# GCC 7 exposed an undefined ____ilog2_NaN reference in upstream 4.9.
# Author HEAD already includes this compatibility correction; synchronize it.
if ! cmp -s "$CFS_SOURCE/include/linux/log2.h" "$ULE_SOURCE/include/linux/log2.h"; then
    diff -u --label a/include/linux/log2.h --label b/include/linux/log2.h "$CFS_SOURCE/include/linux/log2.h" "$ULE_SOURCE/include/linux/log2.h" > "$BUILD_ROOT/logs/cfs-gcc7-log2.patch" || [[ $? == 1 ]]
    cp "$ULE_SOURCE/include/linux/log2.h" "$CFS_SOURCE/include/linux/log2.h"
fi
python3 "$SCRIPT_DIR/apply_ule_compat.py" "$ULE_SOURCE" "$BUILD_ROOT/logs"
COMMON_OBJECT="$BUILD_ROOT/objects/common-config"
mkdir -p -- "$COMMON_OBJECT"

# 只用 CFS 源码生成一次基础配置，再复制给两种内核。
{
    kmake "$CFS_SOURCE" "$COMMON_OBJECT" defconfig
    kmake "$CFS_SOURCE" "$COMMON_OBJECT" kvm_guest.config
    for symbol in SMP MODULES BLK_DEV_INITRD RD_GZIP RD_XZ BLOCK BLK_DEV \
        PCI PCI_MSI VIRTIO VIRTIO_PCI VIRTIO_PCI_LEGACY VIRTIO_BLK VIRTIO_NET \
        VIRTIO_CONSOLE EXT4_FS EXT4_FS_POSIX_ACL EXT4_FS_SECURITY \
        DEVTMPFS DEVTMPFS_MOUNT TTY SERIAL_8250 SERIAL_8250_CONSOLE \
        PROC_FS SYSFS SYSCTL NET NETDEVICES NET_CORE INET UNIX \
        BINFMT_ELF BINFMT_SCRIPT CGROUPS CGROUP_SCHED RT_GROUP_SCHED FAIR_GROUP_SCHED \
        CFS_BANDWIDTH CGROUP_CPUACCT CPUSETS DEBUG_KERNEL DEBUG_FS FTRACE FTRACE_SYSCALLS; do
        "$CFS_SOURCE/scripts/config" --file "$COMMON_OBJECT/.config" --enable "$symbol"
    done
    "$CFS_SOURCE/scripts/config" --file "$COMMON_OBJECT/.config" --disable LOCALVERSION_AUTO
    "$CFS_SOURCE/scripts/config" --file "$COMMON_OBJECT/.config" --set-str LOCALVERSION ''
    kmake "$CFS_SOURCE" "$COMMON_OBJECT" olddefconfig
} 2>&1 | tee "$BUILD_ROOT/logs/configure-common.log"
cp -- "$COMMON_OBJECT/.config" "$BUILD_ROOT/artifacts/common.config"

normalize_config() {
    awk '/^(CONFIG_[A-Za-z0-9_]+=|# CONFIG_[A-Za-z0-9_]+ is not set$)/ && $0 !~ /^CONFIG_LOCALVERSION=/' "$1" | sort
}

configure_variant() {
    local variant=$1 source=$2
    local object="$BUILD_ROOT/objects/$variant"
    mkdir -p -- "$object" "$BUILD_ROOT/artifacts/$variant"
    cp -- "$COMMON_OBJECT/.config" "$object/.config"
    "$source/scripts/config" --file "$object/.config" --set-str LOCALVERSION "-paper02-$variant"
    kmake "$source" "$object" olddefconfig 2>&1 | tee "$BUILD_ROOT/logs/configure-$variant.log"
    # 依赖解析之后再次检查，防止 Kconfig 静默去掉必需功能。
    for symbol in SMP MODULES BLK_DEV_INITRD RD_GZIP RD_XZ PCI VIRTIO_PCI \
        VIRTIO_BLK VIRTIO_NET EXT4_FS DEVTMPFS DEVTMPFS_MOUNT \
        SERIAL_8250 SERIAL_8250_CONSOLE PROC_FS SYSFS SYSCTL \
        CGROUPS CGROUP_SCHED RT_GROUP_SCHED FAIR_GROUP_SCHED CFS_BANDWIDTH CGROUP_CPUACCT CPUSETS DEBUG_KERNEL DEBUG_FS FTRACE FTRACE_SYSCALLS; do
        grep -qx "CONFIG_$symbol=y" "$object/.config" || {
            printf '%s 缺少必需配置：CONFIG_%s=y\n' "$variant" "$symbol" >&2
            return 1
        }
    done
    cp -- "$object/.config" "$BUILD_ROOT/artifacts/$variant/config"
    normalize_config "$object/.config" > "$BUILD_ROOT/artifacts/$variant/config.normalized"
}

configure_variant cfs "$CFS_SOURCE"
configure_variant ule "$ULE_SOURCE"
diff -u "$BUILD_ROOT/artifacts/cfs/config.normalized" \
    "$BUILD_ROOT/artifacts/ule/config.normalized" \
    > "$BUILD_ROOT/logs/config-diff.txt" || {
        echo '两种内核的有效配置存在非 LOCALVERSION 差异，停止构建。' >&2
        cat "$BUILD_ROOT/logs/config-diff.txt" >&2
        exit 1
    }
[[ "$mode" != configure ]] || { echo '已完成配置与一致性检查，尚未编译内核。'; exit 0; }

build_variant() {
    local variant=$1 source=$2 sha=$3
    local object="$BUILD_ROOT/objects/$variant"
    local artifact="$BUILD_ROOT/artifacts/$variant"
    local release stage
    kmake "$source" "$object" -j"$JOBS" bzImage modules 2>&1 | tee "$BUILD_ROOT/logs/build-$variant.log"
    release=$(kmake "$source" "$object" -s --no-print-directory kernelrelease)
    [[ "$release" == "4.9.0-paper02-$variant" ]] || {
        printf '非预期内核版本：%s\n' "$release" >&2
        return 1
    }
    stage=$(mktemp -d "$BUILD_ROOT/modules-stage/$variant.XXXXXX")
    # modules_install 的目标仅为临时打包目录，绝不写宿主 /lib/modules。
    kmake "$source" "$object" INSTALL_MOD_PATH="$stage" DEPMOD=true modules_install \
        2>&1 | tee "$BUILD_ROOT/logs/modules-$variant.log"
    tar -C "$stage" --exclude='./lib/modules/*/build' --exclude='./lib/modules/*/source' \
        -czf "$artifact/modules-$release.tar.gz" ./lib/modules
    cp -- "$object/arch/x86/boot/bzImage" "$artifact/bzImage"
    cp -- "$object/System.map" "$artifact/System.map"
    cp -- "$object/.config" "$artifact/config"
    printf '%s\n' "$release" > "$artifact/kernelrelease.txt"
    {
        printf 'variant=%s\nsource_sha=%s\nkernelrelease=%s\n' "$variant" "$sha" "$release"
        printf 'built_utc=%s\n' "$(date -u +%FT%TZ)"
        printf 'cc=%s\nhostcc=%s\n' "$CC" "$HOSTCC"
        printf 'patches=CFS_log2_GCC7_and_ULE_null_LLC_topology_guard_see_logs\n'
        printf 'execution_status=compiled_not_boot_tested\n'
    } > "$artifact/build-metadata.txt"
    (
        cd -- "$artifact"
        sha256sum bzImage System.map config "modules-$release.tar.gz" > SHA256SUMS
    )
}

if [[ "$mode" == all || "$mode" == cfs ]]; then
    build_variant cfs "$CFS_SOURCE" "$CFS_SHA"
fi
if [[ "$mode" == all || "$mode" == ule ]]; then
    build_variant ule "$ULE_SOURCE" "$ULE_SHA"
fi
printf '构建输出：%s/artifacts；仍需分别生成 initramfs、启动并验证。\n' "$BUILD_ROOT"
