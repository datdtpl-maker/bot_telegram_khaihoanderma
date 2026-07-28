[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

function Initialize-KhdConsole {
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $script:OutputEncoding = $utf8
    [Console]::InputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class KhdConsoleFont {
    [StructLayout(LayoutKind.Sequential)]
    public struct COORD {
        public short X;
        public short Y;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CONSOLE_FONT_INFOEX {
        public uint cbSize;
        public uint nFont;
        public COORD dwFontSize;
        public int FontFamily;
        public int FontWeight;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string FaceName;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetCurrentConsoleFontEx(
        IntPtr consoleOutput,
        bool maximumWindow,
        ref CONSOLE_FONT_INFOEX consoleCurrentFontEx
    );

    public static bool UseConsolas(short height) {
        var info = new CONSOLE_FONT_INFOEX();
        info.cbSize = (uint)Marshal.SizeOf(info);
        info.nFont = 0;
        info.dwFontSize = new COORD { X = 0, Y = height };
        info.FontFamily = 54;
        info.FontWeight = 400;
        info.FaceName = "Consolas";
        return SetCurrentConsoleFontEx(GetStdHandle(-11), false, ref info);
    }
}
'@ -ErrorAction Stop
        [void][KhdConsoleFont]::UseConsolas(18)
    } catch {
        # Không chặn bot nếu máy không cho phép thay đổi font console.
    }
    try {
        $Host.UI.RawUI.WindowTitle = "Bot Khải Hoàn Derma"
    } catch {
    }
}

Initialize-KhdConsole
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
    throw "Chưa tìm thấy Python. Hãy cài Python 3.10 trở lên và chọn Add Python to PATH."
}

$pythonVersion = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$versionParts = $pythonVersion.Trim().Split(".")
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 10)) {
    throw "Bot yêu cầu Python 3.10 trở lên. Phiên bản hiện tại: $pythonVersion"
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
    throw "Thiếu biến cấu hình trong telegram_bot.env: $($missingVariables -join ', ')"
}

& $pythonCommand.Source -c "import gdown; import requests; import google.auth; import google_auth_oauthlib; import importlib.metadata as m, re; s=m.version('gdown'); v=tuple(map(int,re.findall(r'\d+',s)[:2])); assert v >= (6,1), f'gdown {s} quá cũ; cần gdown >= 6.1'"
if ($LASTEXITCODE -ne 0) {
    throw "Thiếu thư viện Python. Chạy: python -m pip install --upgrade -r requirements.txt"
}

if ($ValidateOnly) {
    Write-Host "STARTUP_VALIDATION=PASS"
    Write-Host "Python: $pythonVersion"
    Write-Host "Các file, biến cấu hình và thư viện bắt buộc đều hợp lệ."
    exit 0
}

Write-Host "=============================================="
Write-Host "       KHỞI ĐỘNG BOT KHẢI HOÀN DERMA"
Write-Host "=============================================="
Write-Host "Khởi động Bot Khải Hoàn Derma"
Write-Host "Thư mục: $PSScriptRoot"
Write-Host "Python: $pythonVersion"
Write-Host "Nhấn Ctrl+C để dừng bot an toàn."
Write-Host ""

$consecutiveFastCrashes = 0
while ($true) {
    $startedAt = Get-Date
    & $pythonCommand.Source (Join-Path $PSScriptRoot "telegram_woocommerce_bot.py")
    $exitCode = $LASTEXITCODE
    $runSeconds = ((Get-Date) - $startedAt).TotalSeconds

    if ($exitCode -eq 0) {
        Write-Host "Bot đã dừng bình thường."
        break
    }
    if ($exitCode -eq 99) {
        Write-Host "Đã có một bản bot khác đang chạy. Script sẽ không khởi động trùng."
        break
    }

    if ($runSeconds -lt 20) {
        $consecutiveFastCrashes++
    } else {
        $consecutiveFastCrashes = 0
    }
    if ($consecutiveFastCrashes -ge 5) {
        throw "Bot lỗi liên tiếp 5 lần. Đã dừng để tránh vòng lặp; kiểm tra bot.log."
    }

    $delaySeconds = [Math]::Min(60, 10 + ($consecutiveFastCrashes * 10))
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Bot dừng với exit code $exitCode. Khởi động lại sau $delaySeconds giây..."
    Start-Sleep -Seconds $delaySeconds
}
