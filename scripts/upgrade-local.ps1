<#
.SYNOPSIS
  Upgrade the local PostgreSQL schema safely: backup -> preflight -> upgrade -> verify [-> restart API].

.DESCRIPTION
  Reads connection settings from .env.local (POSTGRES_HOST/PORT/USER/PASSWORD/DB), the same
  variables the API uses. Nothing is changed when the schema is already at head. Before any
  change a custom-format pg_dump is written and checked with pg_restore --list. On failure the
  script stops and prints the exact restore command; it never restores automatically.

  -RestartApi restarts only a process listening on the API port whose command line is
  "uvicorn app:app"; any other process on that port is left alone. Run it from a terminal:
  the restarted API inherits the console, so piping this script's output (e.g. into grep)
  keeps the pipe open until the API stops.

  DOTTY_MIGRATION_LOCK_TIMEOUT (default 5s) bounds how long a migration step waits for a
  table lock; a blocked upgrade rolls back and the version does not move.

.EXAMPLE
  pwsh scripts/upgrade-local.ps1
  pwsh scripts/upgrade-local.ps1 -RestartApi
#>
[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env.local"),
    [string]$PgBin = (Join-Path $env:LOCALAPPDATA "Programs\PostgreSQL\pgsql\bin"),
    [string]$BackupDir = (Join-Path $env:LOCALAPPDATA "dotty-tutor\backups"),
    [int]$KeepBackups = 10,
    [switch]$RestartApi,
    [int]$ApiPort = 8010
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$apiDir = (Resolve-Path (Join-Path $PSScriptRoot "..\apps\api")).Path

function Step([string]$message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Fail([string]$message) { Write-Host "失败：$message" -ForegroundColor Red; exit 1 }

# --- configuration -----------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) { Fail "未找到 $EnvFile；请先复制 .env.local.example 并填写数据库配置。" }
foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
    if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$') {
        $value = $Matches[2] -replace '^"(.*)"$', '$1' -replace "^'(.*)'$", '$1'
        Set-Item -Path "Env:$($Matches[1])" -Value $value
    }
}
foreach ($name in "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB") {
    if (-not (Get-Item "Env:$name" -ErrorAction SilentlyContinue)) { Fail "$EnvFile 缺少 $name。" }
}
# pg_dump / pg_restore read the password from PGPASSWORD; it is never printed.
$env:PGPASSWORD = $env:POSTGRES_PASSWORD
$pgArgs = @("-h", $env:POSTGRES_HOST, "-p", $env:POSTGRES_PORT, "-U", $env:POSTGRES_USER)

$pgDump = Join-Path $PgBin "pg_dump.exe"
$pgRestore = Join-Path $PgBin "pg_restore.exe"
if (-not (Test-Path $pgDump) -or -not (Test-Path $pgRestore)) {
    $pgDump = (Get-Command pg_dump -ErrorAction SilentlyContinue).Source
    $pgRestore = (Get-Command pg_restore -ErrorAction SilentlyContinue).Source
    if (-not $pgDump -or -not $pgRestore) { Fail "找不到 pg_dump/pg_restore；用 -PgBin 指定 PostgreSQL 的 bin 目录。" }
}

$uvDir = Join-Path $env:APPDATA "Python\Python312\Scripts"
if (Test-Path (Join-Path $uvDir "uv.exe")) { $env:PATH = "$uvDir;$env:PATH" }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "找不到 uv。" }

function Invoke-Migration([string]$command) {
    Push-Location $apiDir
    try {
        $output = & uv run python -m persistence.migration_cli $command 2>&1
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    $lines = @($output | ForEach-Object { "$_" })
    $text = ($lines -join "`n").Trim()
    # The CLI prints one indented JSON object after any Alembic log lines.
    $json = $null
    $start = [Array]::IndexOf($lines, "{")
    if ($start -ge 0) {
        try { $json = ($lines[$start..($lines.Count - 1)] -join "`n") | ConvertFrom-Json } catch { $json = $null }
    }
    return @{ Code = $code; Text = $text; Json = $json }
}

# --- 1. already current? -----------------------------------------------------------------
Step "检查数据库 $($env:POSTGRES_DB)@$($env:POSTGRES_HOST):$($env:POSTGRES_PORT) 的 schema 版本"
$state = Invoke-Migration "current"
if ($state.Code -ne 0 -or -not $state.Json) { Fail "无法读取当前版本：`n$($state.Text)" }
$current = $state.Json.current
$head = $state.Json.head
Write-Host "    当前：$current    代码要求：$head"
$needsUpgrade = $current -ne $head

if ($needsUpgrade) {
    # --- 2. read-only preflight ----------------------------------------------------------
    Step "迁移预检（只读）"
    $preflight = Invoke-Migration "preflight"
    if ($preflight.Code -ne 0) { Fail "预检未通过，未做任何修改：`n$($preflight.Text)" }

    # --- 3. backup, then prove the dump is readable --------------------------------------
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $label = if ($current) { $current } else { "empty" }
    $backup = Join-Path $BackupDir "$($env:POSTGRES_DB)-$stamp-$label.dump"
    Step "备份到 $backup"
    # A table held by DDL elsewhere would otherwise make the backup wait indefinitely.
    & $pgDump @pgArgs -d $env:POSTGRES_DB -Fc --lock-wait-timeout=10s -f $backup
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $backup) -or (Get-Item $backup).Length -eq 0) {
        Fail "pg_dump 失败（可能有表被其他操作长时间锁住），未做任何修改。"
    }
    & $pgRestore --list $backup | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "备份文件无法读取，未做任何修改：$backup" }
    Write-Host "    备份大小：$([math]::Round((Get-Item $backup).Length / 1MB, 2)) MB"

    $restoreHint = "如需恢复：先停 API，再执行`n  `"$pgRestore`" -h $($env:POSTGRES_HOST) -p $($env:POSTGRES_PORT) -U $($env:POSTGRES_USER) -d $($env:POSTGRES_DB) --clean --if-exists `"$backup`""

    # --- 4. upgrade (transactional; lock_timeout makes a blocked step fail fast) ---------
    Step "升级 schema"
    $upgrade = Invoke-Migration "upgrade"
    if ($upgrade.Code -ne 0) { Fail "升级失败，迁移事务已回滚、版本未前进。`n$($upgrade.Text)`n$restoreHint" }

    # --- 5. verify -----------------------------------------------------------------------
    Step "校验"
    $verify = Invoke-Migration "verify"
    if ($verify.Code -ne 0) { Fail "校验未通过。`n$($verify.Text)`n$restoreHint" }

    # Keep the newest backups for this database only.
    Get-ChildItem $BackupDir -Filter "$($env:POSTGRES_DB)-*.dump" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $KeepBackups | Remove-Item -Force
    Write-Host "    已升级到 $head；备份保留最近 $KeepBackups 份。" -ForegroundColor Green
} else {
    Write-Host "    已是最新，无需迁移，也不备份。" -ForegroundColor Green
}

# --- 6. optional API restart ---------------------------------------------------------------
if ($RestartApi) {
    Step "重启本机 API（端口 $ApiPort）"
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue
    foreach ($owner in ($listeners | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $owner"
        if (-not $process -or $process.CommandLine -notmatch 'uvicorn\s+app:app') {
            Fail "端口 $ApiPort 被非 Dotty API 进程（PID $owner）占用，未做处理。"
        }
        # Stop the `uv run` launcher too, so the old code is fully gone.
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction SilentlyContinue
        Stop-Process -Id $owner -Force
        if ($parent -and $parent.CommandLine -match 'uvicorn\s+app:app') { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    $log = Join-Path $env:LOCALAPPDATA "dotty-tutor\api.log"
    Start-Process -FilePath "uv" -ArgumentList "run", "python", "-m", "uvicorn", "app:app", "--port", "$ApiPort" `
        -WorkingDirectory $apiDir -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Seconds 2
        try { $health = Invoke-WebRequest "http://127.0.0.1:$ApiPort/api/health" -UseBasicParsing -TimeoutSec 5 } catch { $health = $null }
    } until (($health -and $health.StatusCode -eq 200) -or (Get-Date) -gt $deadline)
    if (-not $health -or $health.StatusCode -ne 200) { Fail "API 在 90 秒内未通过健康检查；日志：$log" }
    Write-Host "    API 已用新代码启动，健康检查通过。日志：$log" -ForegroundColor Green
}
