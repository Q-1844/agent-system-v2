# Phase 41 redo driver — detached launcher (survives Claude Code idle pressure reap)
# NOTE: no non-ASCII literals in this file (PS5.1 reads BOM-less ps1 as ANSI).
$root = Split-Path -Parent $PSScriptRoot  # animation-storyboard dir
$p = Start-Process -FilePath 'D:\Delta Force\python.exe' `
    -ArgumentList '_41_redo_run.py','--continue','sess_de45d41170e1' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root '_41_redo_run2.log') `
    -RedirectStandardError (Join-Path $root '_41_redo_run2.err.log') `
    -PassThru
Write-Output ("PID=" + $p.Id)
