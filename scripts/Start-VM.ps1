param(
    [Parameter(Mandatory)][ValidateSet('cfs','ule')][string]$Variant,
    [Parameter(Mandatory)][string]$QemuPath,
    [Parameter(Mandatory)][string]$DiskPath,
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)][ValidatePattern('^(UUID|PARTUUID|LABEL)=[A-Za-z0-9_.-]+$')][string]$RootDevice,
    [ValidateRange(2,64)][int]$Cpus = 4,
    [ValidateRange(1024,65536)][int]$MemoryMiB = 4096,
    [ValidateRange(1024,65535)][int]$SshPort = 22222
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLogs = Join-Path $taskRoot 'local'
$taskQemu = (Resolve-Path -LiteralPath $QemuPath).Path
$taskDisk = (Resolve-Path -LiteralPath $DiskPath).Path
$taskArtifacts = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$taskKernel = Join-Path $taskArtifacts 'bzImage'
$taskInitrd = Join-Path $taskArtifacts 'initrd.img'
foreach ($taskFile in @($taskKernel, $taskInitrd, (Join-Path $taskArtifacts 'kernelrelease.txt'))) {
    if (!(Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw "Missing file: $taskFile" }
}
$taskRelease = (Get-Content -LiteralPath (Join-Path $taskArtifacts 'kernelrelease.txt') -Raw).Trim()
if ($taskRelease -ne "4.9.0-paper02-$Variant") { throw "Kernel release does not match $Variant" }
# QEMU uses commas as option separators. Quoting cannot protect embedded commas.
foreach ($taskPath in @($taskQemu, $taskDisk, $taskArtifacts, $taskLogs)) {
    if ($taskPath -match '[,"\r\n]') { throw 'Paths must not contain commas, quotes or newlines.' }
}
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskPidFile = Join-Path $taskLogs 'qemu.pid'
if (Test-Path -LiteralPath $taskPidFile) {
    $taskExistingPid = [int](Get-Content -LiteralPath $taskPidFile -Raw)
    if (Get-Process -Id $taskExistingPid -ErrorAction SilentlyContinue) {
        throw 'A recorded process is still running. Shut down the previous guest before switching kernels.'
    }
}
$taskSerial = Join-Path $taskLogs "serial-$Variant.log"
$taskArgs = @(
    '-machine', 'pc,accel=whpx,kernel-irqchip=off', '-cpu', 'Nehalem',
    '-smp', "$Cpus,sockets=1,cores=$Cpus,threads=1", '-m', "$MemoryMiB",
    '-drive', "file=$taskDisk,if=virtio,format=qcow2",
    '-netdev', "user,id=net0,hostfwd=tcp:127.0.0.1:$SshPort-:22",
    '-device', 'virtio-net-pci,netdev=net0', '-display', 'none',
    '-serial', "file:$taskSerial", '-kernel', $taskKernel, '-initrd', $taskInitrd,
    '-append', "root=$RootDevice ro console=ttyS0,115200n8"
)
# Start-Process joins ArgumentList; quote each complete argument, including paths with spaces.
$taskQuotedArgs = $taskArgs | ForEach-Object { '"' + $_ + '"' }
$taskProcess = Start-Process -FilePath $taskQemu -ArgumentList $taskQuotedArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $taskLogs "qemu-$Variant.stdout.log") `
    -RedirectStandardError (Join-Path $taskLogs "qemu-$Variant.stderr.log")
$taskProcess.Id | Set-Content -LiteralPath $taskPidFile
[pscustomobject]@{Pid=$taskProcess.Id; Variant=$Variant; Ssh="127.0.0.1:$SshPort"; SerialLog=$taskSerial}
