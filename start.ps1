# Socratic Code-Tutor Pet - Single Startup Script (PowerShell)
# Usage: .\start.ps1 [-StudentId "local-user"] [-DevUI]

param(
    [string]$StudentId = "local-user",
    [switch]$DevUI = $false
)

$ErrorActionPreference = "Continue"
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       Starting Socratic Code-Tutor Pet Stack             " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Python
try {
    $pyVer = python --version 2>&1
    Write-Host "[OK] Python detected: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH." -ForegroundColor Red
    exit 1
}

# 2. Check Node & npm
try {
    $nodeVer = node --version 2>&1
    Write-Host "[OK] Node detected: $nodeVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Node.js is required for the UI." -ForegroundColor Red
    exit 1
}

# 3. FalkorDB / Docker
Write-Host "`n[*] Checking FalkorDB..." -ForegroundColor Yellow
$falkorOnline = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect("127.0.0.1", 6379, $null, $null)
    $success = $iar.AsyncWaitHandle.WaitOne(1000, $false)
    if ($success -and $tcp.Connected) {
        $tcp.EndConnect($iar)
        $tcp.Close()
        $falkorOnline = $true
        Write-Host "[OK] FalkorDB is running on localhost:6379." -ForegroundColor Green
    }
} catch {}

if (-not $falkorOnline) {
    Write-Host "[*] FalkorDB not reachable on 6379. Attempting docker-compose..." -ForegroundColor Yellow
    try {
        docker compose up -d falkordb 2>&1 | Out-Null
        Start-Sleep -Seconds 3
        Write-Host "[OK] FalkorDB container launched via docker compose." -ForegroundColor Green
        $falkorOnline = $true
    } catch {
        Write-Host "[ERROR] Docker not available or failed to start FalkorDB." -ForegroundColor Red
        Write-Host "        FalkorDB is required! Socratic Code-Tutor Pet cannot operate without FalkorDB on localhost:6379." -ForegroundColor Red
        exit 1
    }
}

# 4. Check Ollama
Write-Host "`n[*] Checking Ollama..." -ForegroundColor Yellow
try {
    $res = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 2 -ErrorAction Stop
    Write-Host "[OK] Ollama is active on http://localhost:11434." -ForegroundColor Green
    $models = $res.models | ForEach-Object { $_.name }
    if ($models -contains "qwen2.5-coder:7b" -or ($models | Where-Object { $_ -like "qwen2.5-coder*" })) {
        Write-Host "[OK] Model 'qwen2.5-coder:7b' is available." -ForegroundColor Green
    } else {
        Write-Host "[WARN] 'qwen2.5-coder:7b' not pulled yet. You can run: ollama pull qwen2.5-coder:7b" -ForegroundColor DarkYellow
    }
} catch {
    Write-Host "[WARN] Ollama is offline or not installed." -ForegroundColor DarkYellow
    Write-Host "       Tutor will use high-confidence Socratic seed questions and pattern heuristics." -ForegroundColor DarkYellow
}

# 5. Ingest concepts and ensure demo student data
Write-Host "`n[*] Preparing graph knowledge and student history..." -ForegroundColor Yellow
python -c "import sys; sys.path.insert(0, 'daemon'); from adapters.graph_adapter import GraphAdapter; g = GraphAdapter(); g.ingest_concepts(); print('[OK] Concepts verified.')"
if (-not (Test-Path "$env:USERPROFILE\.cache\tutor-pet\student_history.json")) {
    Write-Host "[*] First run: seeding demo student history..." -ForegroundColor Yellow
    python scripts\seed_demo_student.py --student-id $StudentId
}

# 6. Start Daemon
Write-Host "`n[*] Launching Socratic Tutor Daemon on http://127.0.0.1:7777..." -ForegroundColor Yellow
$daemonProc = Start-Process python -ArgumentList "-m uvicorn app:app --host 127.0.0.1 --port 7777" -WorkingDirectory "daemon" -PassThru -NoNewWindow
Start-Sleep -Seconds 3

# Verify daemon health
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:7777/api/health" -Method Get -TimeoutSec 5
    Write-Host "[OK] Daemon is healthy! Backends: $($health.backends | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Daemon health check took longer than expected; continuing..." -ForegroundColor DarkYellow
}

# 7. Start UI
Write-Host "`n[*] Launching Socratic Pet UI..." -ForegroundColor Yellow
$tauriRelease = "ui\src-tauri\target\release\tutor-pet.exe"
$tauriDebug = "ui\src-tauri\target\debug\tutor-pet.exe"
$petExe = $null

if (-not $DevUI) {
    if (Test-Path $tauriRelease) { $petExe = $tauriRelease }
    elseif (Test-Path $tauriDebug) { $petExe = $tauriDebug }
}

if ($petExe) {
    Write-Host "[OK] Starting Tauri native desktop app ($petExe)..." -ForegroundColor Green
    Start-Process $petExe
} else {
    Write-Host "[OK] Starting UI via Vite server (open in browser or run 'npm run tauri dev' in ui/)..." -ForegroundColor Green
    $uiProc = Start-Process npm -ArgumentList "run dev -- --host 127.0.0.1" -WorkingDirectory "ui" -PassThru -NoNewWindow
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:1420"
    Start-Process "http://localhost:1420/dashboard.html"
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " Socratic Code-Tutor Pet is running!" -ForegroundColor Green
Write-Host "  * Daemon:    http://127.0.0.1:7777 (WS: /ws/editor, /ws/ui)" -ForegroundColor White
Write-Host "  * UI Pet:    http://localhost:5173" -ForegroundColor White
Write-Host "  * Dashboard: http://localhost:5173/dashboard.html" -ForegroundColor White
Write-Host " Press Ctrl+C in this window to stop all services." -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

try {
    while ($true) { Start-Sleep -Seconds 2 }
} finally {
    Write-Host "`n[*] Stopping Tutor Pet services..." -ForegroundColor Yellow
    if ($daemonProc -and -not $daemonProc.HasExited) { Stop-Process -Id $daemonProc.Id -Force }
    if ($uiProc -and -not $uiProc.HasExited) { Stop-Process -Id $uiProc.Id -Force }
    Write-Host "[OK] All services stopped." -ForegroundColor Green
}
