<#
  启动一个带调试端口的 Chrome，供 jobfill 接管填表。

  为什么用独立配置目录？
    1. Chrome 只有在「没有其他实例占用同一配置目录」时才会打开调试端口。
       用独立目录就不用先关掉你日常的 Chrome。
    2. 这个目录会记住登录状态，网申系统登录一次可以长期复用。

  用法：
    .\launch_chrome.ps1                     # 独立配置目录（推荐）
    .\launch_chrome.ps1 -Url "https://xxx.zhiye.com/"   # 顺便打开某个地址
    .\launch_chrome.ps1 -UseDefaultProfile  # 复用你日常的配置（需先完全退出 Chrome）

  参数：
    -Port              调试端口，默认 9222
    -ProfileDir        独立配置目录，默认 %LOCALAPPDATA%\jobfill-chrome
    -UseDefaultProfile 不加 --user-data-dir，直接用你日常的配置
    -Edge              用 Edge 而不是 Chrome
    -Restart           如果端口已被占用，先结束占用它的浏览器进程
#>
[CmdletBinding()]
param(
    [int]$Port = 9222,
    [string]$Url = "",
    [string]$ProfileDir = "$env:LOCALAPPDATA\jobfill-chrome",
    [switch]$UseDefaultProfile,
    [switch]$Edge,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

function Find-Browser {
    param([switch]$PreferEdge)
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $edge = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    $order = if ($PreferEdge) { $edge + $chrome } else { $chrome + $edge }
    foreach ($p in $order) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-Port {
    param([int]$P)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $P)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

$exe = Find-Browser -PreferEdge:$Edge
if (-not $exe) {
    Write-Host "没找到 Chrome 或 Edge，请手动指定浏览器路径。" -ForegroundColor Red
    exit 1
}

if (Test-Port -P $Port) {
    Write-Host "端口 $Port 已经被占用。" -ForegroundColor Yellow
    try {
        $ver = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 3
        Write-Host "  看起来已经是一个可接管的浏览器：$($ver.Browser)" -ForegroundColor Green
        Write-Host "  直接用就行：  py -3 -m jobfill fill" -ForegroundColor Green
        exit 0
    } catch {
        Write-Host "  但它不是浏览器调试端口（可能是别的程序占用了）。" -ForegroundColor Red
        if (-not $Restart) {
            Write-Host "  可以换个端口：  .\launch_chrome.ps1 -Port 9333" -ForegroundColor Yellow
            exit 1
        }
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 800
    }
}

$args = @(
    "--remote-debugging-port=$Port",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate"
)
if ($UseDefaultProfile) {
    Write-Host "使用你日常的 Chrome 配置目录。" -ForegroundColor Yellow
    Write-Host "注意：如果 Chrome 已经在运行，调试端口不会生效 —— 需要先完全退出 Chrome（含托盘）。" -ForegroundColor Yellow
} else {
    New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
    $args += "--user-data-dir=$ProfileDir"
}
if ($Url) { $args += $Url }

Write-Host "启动：$exe" -ForegroundColor Cyan
Write-Host "参数：$($args -join ' ')" -ForegroundColor DarkGray
Start-Process -FilePath $exe -ArgumentList $args | Out-Null

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Port -P $Port) { break }
}

if (Test-Port -P $Port) {
    Write-Host ""
    Write-Host "调试端口 $Port 已就绪。" -ForegroundColor Green
    Write-Host "接下来：" -ForegroundColor Green
    Write-Host "  1) 在这个 Chrome 窗口里登录网申系统（登录/验证码/滑块都你自己来）"
    Write-Host "  2) 把要填的表单页面停在当前标签页"
    Write-Host "  3) 回到终端执行：  py -3 -m jobfill fill --learn"
    if (-not $UseDefaultProfile) {
        Write-Host ""
        Write-Host "（登录状态保存在 $ProfileDir，下次直接复用，不用重新登录）" -ForegroundColor DarkGray
    }
} else {
    Write-Host "端口 $Port 没能起来。" -ForegroundColor Red
    Write-Host "常见原因：Chrome 已经用同一个配置目录在运行。试试：" -ForegroundColor Yellow
    Write-Host "  · 完全退出 Chrome 后重跑本脚本"
    Write-Host "  · 或换端口：.\launch_chrome.ps1 -Port 9333"
    exit 1
}
