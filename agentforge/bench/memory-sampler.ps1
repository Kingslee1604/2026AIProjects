# Samples Windows-side memory every 5 s while bench/measure.sh runs, and keeps the machine
# awake (a sleeping laptop stalls a long run). Stops when the stop file appears.
#   VmmemWSL: the WSL2 virtual machine (Linux, Docker, the stack)
#   ollama:   every Ollama process (server and model runners)
param([string]$Out, [string]$StopFile)

Add-Type -Namespace Bench -Name Power -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
'@
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no sleep while this process runs (display may turn off).
[void][Bench.Power]::SetThreadExecutionState([uint32]"0x80000001")

"epoch_ms,vmmem_mb,ollama_mb" | Out-File -Encoding ascii $Out
while (-not (Test-Path $StopFile)) {
    $vm = (Get-Process -Name VmmemWSL, vmmem -ErrorAction SilentlyContinue |
        Measure-Object WorkingSet64 -Sum).Sum
    $ol = (Get-Process | Where-Object { $_.Name -like '*ollama*' -or $_.Name -like '*llama*' } |
        Measure-Object WorkingSet64 -Sum).Sum
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    "$now,$([math]::Round($vm / 1MB)),$([math]::Round($ol / 1MB))" | Out-File -Append -Encoding ascii $Out
    Start-Sleep -Seconds 5
}
[void][Bench.Power]::SetThreadExecutionState([uint32]"0x80000000")
