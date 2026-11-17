param([ValidateSet('build','cfs','ule')][string]$Variant='build')
$ErrorActionPreference='Stop'
$paper02Root=$PSScriptRoot
$paper02VmDir=Join-Path $paper02Root 'vm'
$paper02QemuDir=Join-Path $paper02Root 'tools\qemu'
$paper02PidFile=Join-Path $paper02VmDir 'qemu.pid'
if (Test-Path -LiteralPath $paper02PidFile) {
    $paper02Existing=[int](Get-Content -LiteralPath $paper02PidFile)
    if (Get-Process -Id $paper02Existing -ErrorAction SilentlyContinue) {
        throw 'A recorded VM process is still running. Stop it before changing kernels.'
    }
}
$paper02Args=@('-L',"$paper02QemuDir\share\qemu",'-machine','pc,accel=whpx,kernel-irqchip=off','-cpu','Nehalem','-smp','4,sockets=1,cores=4,threads=1','-m','4096','-drive',"file=$paper02VmDir\build.qcow2,if=virtio,format=qcow2",'-netdev','user,id=net0,hostfwd=tcp:127.0.0.1:22222-:22','-device','virtio-net-pci,netdev=net0','-display','none','-serial',"file:$paper02VmDir\serial-$Variant.log",'-qmp','tcp:127.0.0.1:4445,server=on,wait=off')
if($Variant -eq 'build') {
    $paper02Args+=@('-drive',"file=$paper02VmDir\seed.iso,media=cdrom,readonly=on")
} else {
    $paper02Args+=@('-drive',"file=$paper02VmDir\seed.iso,media=cdrom,readonly=on")
    $paper02Artifact=Join-Path $paper02Root "artifacts\$Variant"
    foreach($paper02File in @('bzImage','initrd.img')) {
        if(!(Test-Path -LiteralPath (Join-Path $paper02Artifact $paper02File))) {throw "Missing $paper02File"}
    }
    $paper02Args+=@('-kernel',"$paper02Artifact\bzImage",'-initrd',"$paper02Artifact\initrd.img",'-append','"root=PARTUUID=94b4eafe-4fc5-4e08-b832-d023be514ab9 ro console=ttyS0,115200n8"')
}
$paper02Vm=Start-Process -FilePath "$paper02QemuDir\qemu-system-x86_64.exe" -ArgumentList $paper02Args -WindowStyle Hidden -PassThru -RedirectStandardOutput "$paper02VmDir\qemu-$Variant.stdout.log" -RedirectStandardError "$paper02VmDir\qemu-$Variant.stderr.log"
$paper02Vm.Id | Set-Content -LiteralPath $paper02PidFile
[pscustomobject]@{Pid=$paper02Vm.Id;Variant=$Variant;Ssh='127.0.0.1:22222'}
