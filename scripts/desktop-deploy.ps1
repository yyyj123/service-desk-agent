param([ValidateSet('Start','Stop')][string]$Action = 'Start', [switch]$NoBrowser)
. (Join-Path $PSScriptRoot 'desktop-common.ps1')
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$envFile = Join-Path $root '.env'
$lock = $null
try {
    $lock = [IO.File]::Open((Join-Path $root '.env.desktop.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    Assert-DesktopDocker
    if (-not (Test-Path -LiteralPath $envFile)) {
        if ($Action -eq 'Stop') { Write-Host 'No local deployment configuration found.'; exit 0 }
        Write-NewDesktopFile $envFile ([IO.File]::ReadAllText((Join-Path $root '.env.example')))
    }
    $enterprise = Join-Path $root '.env.enterprise'
    if (-not (Test-Path -LiteralPath $enterprise)) {
        if ($Action -eq 'Stop') { Write-Host 'No local deployment secrets found.'; exit 0 }
        Write-NewDesktopFile $enterprise ("IT_JWT_SECRET=$(New-DesktopSecret)`nIT_METRICS_TOKEN=$(New-DesktopSecret)`nIT_ORIGIN=http://127.0.0.1:8600`nIT_COOKIE_SECURE=false`n")
    }
    $script:Compose = @('compose','--project-name','service-desk-desktop','--project-directory',$root,'--env-file',$envFile,'-f','compose.yaml')
    $script:ContainerPort = 8600
    Invoke-DesktopCompose @('config','--quiet')
    if ($Action -eq 'Stop') {
        Invoke-DesktopCompose @('stop')
        Write-Host 'Stopped. Accounts, knowledge and conversations are preserved.'
        exit 0
    }
    $values = Read-DesktopEnv $envFile
    if (-not $values['IT_CHAT_KEY'] -or -not $values['IT_EMBED_KEY']) {
        Write-Host 'First launch: fill IT_CHAT_KEY / IT_EMBED_KEY, provider URLs and model names in .env, then save.'
        Start-Process notepad.exe -ArgumentList ('"' + $envFile + '"')
        [void](Read-Host 'After saving .env, press Enter')
        $values = Read-DesktopEnv $envFile
        if (-not $values['IT_CHAT_KEY'] -or -not $values['IT_EMBED_KEY']) {
            throw 'Model API keys are still missing. Edit .env and launch again.'
        }
    }
    Assert-DesktopPort 8600 'app'
    Invoke-DesktopCompose @('build','app')
    Invoke-DesktopCompose @('run','--rm','app','python','-m','service.manage','init')
    # Mark only a completed ingest; retry failed provider calls on the next launch.
    $seed = 'from service.config import ROOT,Settings; from service.retrieval import KnowledgeBase; import hashlib; s=Settings(); s.validate(); k=KnowledgeBase(s); fingerprint=hashlib.sha256((s.database_url+s.embed_url+s.embed_model).encode()).hexdigest(); marker=s.data_dir/(".desktop-ingested-"+fingerprint); k.ingest(ROOT/"knowledge") if not marker.exists() else None; marker.touch()'
    $seed = $seed.Replace('"', "'")
    Invoke-DesktopCompose @('run','--rm','app','python','-c',$seed)
    Invoke-DesktopCompose @('up','-d','--wait','--wait-timeout','300','app')
    Wait-DesktopHttp 'http://127.0.0.1:8600/health/ready'
    $runtime = Join-Path $root 'runtime'
    [void](New-Item -ItemType Directory -Path $runtime -Force)
    Invoke-DesktopCompose @('cp','app:/app/runtime/accounts.txt',(Join-Path $runtime 'accounts.txt'))
    Write-Host 'Ready: http://127.0.0.1:8600'
    Write-Host 'Employee/admin passwords: runtime/accounts.txt (local private file).'
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8600' }
} catch {
    Write-Host ("Launch failed: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($lock) { $lock.Dispose() }
}
