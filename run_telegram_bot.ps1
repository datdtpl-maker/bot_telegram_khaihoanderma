[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$requiredFiles = @(
    "telegram_woocommerce_bot.py",
    "notion_sync.py",
    "telegram_bot.env",
    "requirements.txt"
)
foreach ($name in $requiredFiles) {
    $path = Join-Path $PSScriptRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Thiếu file bắt buộc: $path"
    }
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Chua tim thay Python. Hay cai Python 3.10 tro len va chon Add Python to PATH."
}

$pythonVersion = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$versionParts = $pythonVersion.Trim().Split(".")
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 10)) {
    throw "Bot yeu cau Python 3.10 tro len. Phien ban hien tai: $pythonVersion"
}

$envFile = Join-Path $PSScriptRoot "telegram_bot.env"
Get-Content -Encoding UTF8 -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    $parts = $line.Split("=", 2)
    if ($parts.Count -eq 2) {
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

$requiredVariables = @(
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "WORDPRESS_SITE_URL",
    "WORDPRESS_USERNAME",
    "WORDPRESS_PASSWORD",
    "WOOCOMMERCE_CONSUMER_KEY",
    "WOOCOMMERCE_CONSUMER_SECRET",
    "NOTION_TOKEN",
    "NOTION_DATABASE_ID"
)
$missingVariables = @($requiredVariables | Where-Object { -not [Environment]::GetEnvironmentVariable($_, "Process") })
if ($missingVariables.Count -gt 0) {
    throw "Thieu bien cau hinh trong telegram_bot.env: $($missingVariables -join ', ')"
}

& $pythonCommand.Source -c "import gdown; import requests; import google.auth; import google_auth_oauthlib"
if ($LASTEXITCODE -ne 0) {
    throw "Thieu thu vien Python. Chay: python -m pip install -r requirements.txt"
}

if ($ValidateOnly) {
    Write-Host "STARTUP_VALIDATION=PASS"
    Write-Host "Python: $pythonVersion"
    Write-Host "Cac file, bien cau hinh va thu vien bat buộc deu hop le."
    exit 0
}

Write-Host "Khoi dong Bot Khai Hoan Derma"
Write-Host "Thu muc: $PSScriptRoot"
Write-Host "Python: $pythonVersion"
Write-Host "Nhan Ctrl+C de dung bot an toan."
Write-Host ""

$consecutiveFastCrashes = 0
while ($true) {
    $startedAt = Get-Date
    & $pythonCommand.Source (Join-Path $PSScriptRoot "telegram_woocommerce_bot.py")
    $exitCode = $LASTEXITCODE
    $runSeconds = ((Get-Date) - $startedAt).TotalSeconds

    if ($exitCode -eq 0) {
        Write-Host "Bot da dung binh thuong."
        break
    }
    if ($exitCode -eq 99) {
        Write-Host "Da co mot ban bot khac dang chay. Script se khong khoi dong trung."
        break
    }

    if ($runSeconds -lt 20) {
        $consecutiveFastCrashes++
    } else {
        $consecutiveFastCrashes = 0
    }
    if ($consecutiveFastCrashes -ge 5) {
        throw "Bot loi lien tiep 5 lan. Da dung de tranh vong lap; kiem tra bot.log."
    }

    $delaySeconds = [Math]::Min(60, 10 + ($consecutiveFastCrashes * 10))
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Bot dung voi exit code $exitCode. Khoi dong lai sau $delaySeconds giay..."
    Start-Sleep -Seconds $delaySeconds
}
