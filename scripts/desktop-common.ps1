# Shared Windows PowerShell 5.1 helpers. Importing this file does not start services.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-DesktopSecret {
    $bytes = New-Object byte[] 36
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Write-NewDesktopFile([string]$Path, [string]$Content) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($Content)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
}

function Read-DesktopEnv([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Get-DesktopPort($Values, [string]$Key, [int]$Default) {
    $port = $Default
    if ($Values.ContainsKey($Key)) {
        if (-not [int]::TryParse($Values[$Key], [ref]$port)) { throw "Invalid port in $Key." }
    }
    if ($port -lt 1024 -or $port -gt 65535) { throw "$Key must be between 1024 and 65535." }
    return $port
}

function Test-DesktopDocker {
    $process = New-Object Diagnostics.Process
    $process.StartInfo.FileName = (Get-Command docker -ErrorAction Stop).Source
    $process.StartInfo.Arguments = 'info --format "{{.OSType}}"'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        [void]$process.Start()
        if (-not $process.WaitForExit(10000)) { $process.Kill(); return $false }
        return ($process.ExitCode -eq 0 -and $process.StandardOutput.ReadToEnd().Trim() -eq 'linux')
    } finally { $process.Dispose() }
}

function Assert-DesktopDocker {
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $command) {
        foreach ($candidate in $candidates) {
            $bin = Join-Path (Split-Path -Parent $candidate) 'resources\bin'
            if (Test-Path -LiteralPath (Join-Path $bin 'docker.exe')) {
                $env:PATH = "$bin;$env:PATH"
                break
            }
        }
        $command = Get-Command docker -ErrorAction SilentlyContinue
    }
    if (-not $command) {
        $installUrl = 'https://docs.docker.com/desktop/setup/install/windows-install/'
        Write-Host "Install Docker Desktop first (WSL 2 / Linux containers): $installUrl"
        Start-Process $installUrl
        throw 'After installation and any required restart, double-click the launcher again.'
    }
    if (-not (Test-DesktopDocker)) {
        $candidates += Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $command.Source))) 'Docker Desktop.exe'
        $desktop = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($desktop) { Start-Process -FilePath $desktop -WindowStyle Hidden }
        Write-Host 'Waiting for Docker Desktop / Linux engine (up to 2 minutes)...'
        $deadline = (Get-Date).AddMinutes(2)
        while (-not (Test-DesktopDocker)) {
            if ((Get-Date) -gt $deadline) {
                throw 'Docker is not ready. Open Docker Desktop, finish WSL 2 setup, switch to Linux containers, then retry.'
            }
            Start-Sleep -Seconds 3
        }
    }
    $versionText = (& docker compose version --short | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Install/update Docker Desktop with Docker Compose.' }
    $version = [regex]::Match($versionText, '\d+\.\d+\.\d+')
    if (-not $version.Success -or [version]$version.Value -lt [version]'2.24.4') {
        throw 'Update Docker Desktop: Docker Compose 2.24.4 or newer is required.'
    }
    Remove-Item Env:COMPOSE_PROFILES -ErrorAction SilentlyContinue
    Remove-Item Env:COMPOSE_FILE -ErrorAction SilentlyContinue
}

function Invoke-DesktopCompose([string[]]$Items) {
    & docker @script:Compose @Items
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($Items[0]). Review the output above and retry." }
}

function Assert-DesktopPort([int]$Port, [string]$Service) {
    $ids = @(& docker @script:Compose ps -q $Service)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing containers.' }
    if ($ids.Count -gt 0) {
        $bindings = & docker @script:Compose port $Service $script:ContainerPort 2>$null
        if ($LASTEXITCODE -eq 0 -and "$bindings" -match (':' + $Port + '\s*$')) { return }
    }
    $ports = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port
    if ($Port -in $ports) { throw "Port $Port is in use by another application. Stop it or change the port in the environment file." }
}

function Wait-DesktopHttp([string]$Url) {
    $deadline = (Get-Date).AddMinutes(3)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Services started, but $Url is not ready. Check Docker Desktop container logs and retry."
}
