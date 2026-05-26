$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ErrorActionPreference = "Stop"

$envFile = Join-Path $PSScriptRoot "telegram_bot.env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Khong tim thay file cau hinh: $envFile"
}

Get-Content -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    $parts = $line.Split("=", 2)
    if ($parts.Count -eq 2) {
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

Write-Host "Dang khoi dong Telegram WooCommerce Bot..."
Write-Host "Thu muc: $PSScriptRoot"
Write-Host "Kiem tra Python:"
python --version
Write-Host ""

while ($true) {
    python (Join-Path $PSScriptRoot "telegram_woocommerce_bot.py")
    $exitCode = $LASTEXITCODE
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Bot vua dung voi exit code $exitCode. Tu khoi dong lai sau 30 giay..."
    Start-Sleep -Seconds 30
}
