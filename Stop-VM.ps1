param([switch]$Immediate)
$ErrorActionPreference='Stop'
$paper02Client=[Net.Sockets.TcpClient]::new('127.0.0.1',4445)
$paper02Stream=$paper02Client.GetStream()
$paper02Stream.ReadTimeout=5000
$paper02Reader=[IO.StreamReader]::new($paper02Stream)
$paper02Writer=[IO.StreamWriter]::new($paper02Stream)
$paper02Writer.AutoFlush=$true
try {
    $null=$paper02Reader.ReadLine()
    $paper02Writer.WriteLine('{"execute":"qmp_capabilities"}')
    do { $paper02Reply=$paper02Reader.ReadLine() } until($paper02Reply -match '"return"|"error"')
    if($Immediate) { $paper02Writer.WriteLine('{"execute":"quit"}') } else { $paper02Writer.WriteLine('{"execute":"system_powerdown"}') }
    Write-Output 'Guest ACPI shutdown requested. Wait for the VM process to exit before restarting.'
} finally { $paper02Client.Close() }
