param(
    [string]$AdbPath = "adb",
    [string]$AdbAddress = "",
    [string]$DeviceSerial = ""
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$bridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = Split-Path -Parent $bridgeDir
$venvDir = Join-Path $bridgeDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$configPath = Join-Path $bridgeDir "config.json"
$apkPath = Join-Path $repoDir "com.ximalaya.ting.android.xmloader.XMApplication.apk"
$packageName = "com.ximalaya.ting.android"
$supportedVersion = "9.4.52.3"
$remoteServer = "/data/local/tmp/audioflow-frida-server"

function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $prefix = @()
    if ($script:DeviceSerial) {
        $prefix = @("-s", $script:DeviceSerial)
    }
    & $script:AdbPath @prefix @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "ADB 命令失败：adb $($Arguments -join ' ')"
    }
}

function New-BridgeToken {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

Write-Host "[1/7] 检查 Python 和 ADB..."
$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pythonLauncher) {
    $basePython = $pythonLauncher.Source
    $baseArgs = @("-3")
} else {
    $pythonLauncher = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonLauncher) {
        throw "没有找到 Python 3，请先安装 Python 3.10 或更高版本。"
    }
    $basePython = $pythonLauncher.Source
    $baseArgs = @()
}

$adbCommand = Get-Command $AdbPath -ErrorAction SilentlyContinue
if (-not $adbCommand) {
    throw "没有找到 adb。请把模拟器的 adb.exe 加入 PATH，或使用 -AdbPath 指定完整路径。"
}
$script:AdbPath = $adbCommand.Source

if ($AdbAddress) {
    Write-Host "[2/7] 连接模拟器 $AdbAddress..."
    & $script:AdbPath connect $AdbAddress
    if ($LASTEXITCODE -ne 0) { throw "无法连接模拟器 $AdbAddress" }
    if (-not $DeviceSerial) { $DeviceSerial = $AdbAddress }
} else {
    Write-Host "[2/7] 使用当前 ADB 设备..."
}
$script:DeviceSerial = $DeviceSerial
Invoke-Adb -Arguments @("get-state") | Out-Null

Write-Host "[3/7] 准备 Bridge Python 环境..."
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $basePython @baseArgs -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "创建 Python 虚拟环境失败" }
}
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $bridgeDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "安装 Frida 依赖失败" }

Write-Host "[4/7] 检查喜马拉雅 App..."
$packageDump = (Invoke-Adb -Arguments @("shell", "dumpsys", "package", $packageName)) -join "`n"
$versionMatch = [regex]::Match($packageDump, 'versionName=([^\s]+)')
if (-not $versionMatch.Success) {
    if (-not (Test-Path -LiteralPath $apkPath)) {
        throw "模拟器未安装喜马拉雅，仓库中也没有指定版本 APK。"
    }
    Write-Host "      未安装，正在安装仓库内的 $supportedVersion..."
    Invoke-Adb -Arguments @("install", "-r", $apkPath) | Out-Null
} elseif ($versionMatch.Groups[1].Value -ne $supportedVersion) {
    throw "当前喜马拉雅版本是 $($versionMatch.Groups[1].Value)，Bridge 仅支持 $supportedVersion。请卸载后安装仓库内 APK。"
}

Write-Host "[5/7] 安装并启动 Frida Server..."
$rootCheck = (Invoke-Adb -Arguments @("shell", "su", "-c", "id")) -join ""
if ($rootCheck -notmatch 'uid=0') {
    throw "模拟器没有开放 Root/su。请在模拟器设置中开启 Root 后重启模拟器。"
}
$fridaVersion = (& $venvPython -c "import frida; print(frida.__version__)").Trim()
$abi = ((Invoke-Adb -Arguments @("shell", "getprop", "ro.product.cpu.abi")) -join "").Trim()
$fridaArch = switch -Regex ($abi) {
    '^arm64' { 'arm64'; break }
    '^armeabi|^arm' { 'arm'; break }
    '^x86_64' { 'x86_64'; break }
    '^x86' { 'x86'; break }
    default { throw "不支持的模拟器 CPU 架构：$abi" }
}
$serverRunning = ((Invoke-Adb -Arguments @("shell", "su", "-c", "pidof audioflow-frida-server || true")) -join "").Trim()
if (-not $serverRunning) {
    $tempDir = Join-Path ([IO.Path]::GetTempPath()) "audioflow-bridge-frida-$fridaVersion-$fridaArch"
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $archiveName = "frida-server-$fridaVersion-android-$fridaArch.xz"
    $archivePath = Join-Path $tempDir $archiveName
    $serverPath = Join-Path $tempDir "frida-server-$fridaVersion-android-$fridaArch"
    if (-not (Test-Path -LiteralPath $serverPath)) {
        $downloadUrl = "https://github.com/frida/frida/releases/download/$fridaVersion/$archiveName"
        Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath
        & tar -xf $archivePath -C $tempDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $serverPath)) {
            throw "解压 Frida Server 失败，请确认系统 tar 支持 xz。"
        }
    }
    Invoke-Adb -Arguments @("push", $serverPath, $remoteServer) | Out-Null
    Invoke-Adb -Arguments @("shell", "su", "-c", "chmod 755 $remoteServer")
    Invoke-Adb -Arguments @("shell", "su", "-c", "nohup $remoteServer >/dev/null 2>&1 &")
    Start-Sleep -Seconds 1
}
Invoke-Adb -Arguments @("forward", "tcp:27042", "tcp:27042") | Out-Null

Write-Host "[6/7] 生成配置并启动喜马拉雅..."
if (-not (Test-Path -LiteralPath $configPath)) {
    $config = Get-Content -Raw (Join-Path $bridgeDir "config.example.json") | ConvertFrom-Json
    $config.token = New-BridgeToken
    $configJson = $config | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($configPath, $configJson, (New-Object Text.UTF8Encoding($false)))
}
Invoke-Adb -Arguments @("shell", "monkey", "-p", $packageName, "-c", "android.intent.category.LAUNCHER", "1") | Out-Null
$config = Get-Content -Raw $configPath | ConvertFrom-Json
$lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Sort-Object InterfaceMetric |
    Select-Object -First 1 -ExpandProperty IPAddress
if (-not $lanIp) { $lanIp = "这台电脑的局域网IP" }

Write-Host ""
Write-Host "[7/7] Bridge 即将启动。首次使用请在模拟器内登录喜马拉雅并播放任意音频一次。" -ForegroundColor Green
Write-Host "NAS .env 请填写：" -ForegroundColor Cyan
Write-Host "XIMALAYA_TICKET_PROVIDER_URL=http://${lanIp}:$($config.port)/ximalaya/ticket"
Write-Host "XIMALAYA_TICKET_PROVIDER_TOKEN=<与 Bridge 配置相同，已隐藏>"
Write-Host "如果 NAS 无法连接，请在 Windows 防火墙允许 TCP $($config.port)。"
Write-Host ""

Set-Location $repoDir
& $venvPython -m bridge.server --config $configPath
