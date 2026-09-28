# Phase 41 repair test - detached launcher (ASCII only; PS5.1 reads BOM-less as ANSI)
$root = Split-Path -Parent $PSScriptRoot  # animation-storyboard dir
$p = Start-Process -FilePath 'D:\Delta Force\python.exe' `
    -ArgumentList '_41_repair_run.py' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root '_41_repair_run.log') `
    -RedirectStandardError (Join-Path $root '_41_repair_run.err.log') `
    -PassThru
Write-Output ("PID=" + $p.Id)
