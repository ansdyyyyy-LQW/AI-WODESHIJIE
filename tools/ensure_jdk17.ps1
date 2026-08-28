param(
    [string]$Target = ".runtime\jdk17"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Test-Java17([string]$JavaExe) {
    if (-not (Test-Path $JavaExe)) { return $false }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $JavaExe
    $startInfo.Arguments = "-version"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $version = $process.StandardError.ReadToEnd() + $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    return ($process.ExitCode -eq 0 -and $version -match 'version "17\.')
}

$targetPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Target))
$java = Join-Path $targetPath "bin\java.exe"
if (Test-Java17 $java) {
    Write-Output $targetPath
    exit 0
}

$systemJava = Get-Command java.exe -ErrorAction SilentlyContinue
if ($systemJava -and (Test-Java17 $systemJava.Source)) {
    Write-Output (Split-Path (Split-Path $systemJava.Source -Parent) -Parent)
    exit 0
}

$installedCandidates = @(
    Get-ChildItem -LiteralPath "C:\Program Files\Microsoft" -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath "C:\Program Files\Eclipse Adoptium" -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue
)
foreach ($candidate in $installedCandidates) {
    $candidateJava = Join-Path $candidate.FullName "bin\java.exe"
    if (Test-Java17 $candidateJava) {
        Write-Output $candidate.FullName
        exit 0
    }
}

$runtimeRoot = Split-Path $targetPath -Parent
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$archive = Join-Path $runtimeRoot "jdk17.zip"
$temp = Join-Path $runtimeRoot "jdk17-extract"
Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $temp | Out-Null
Write-Host "[INFO] Downloading a private JDK 17 for this project..."
$url = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk"
Invoke-WebRequest $url -OutFile $archive
Expand-Archive -Force $archive $temp
$folder = Get-ChildItem $temp -Directory | Select-Object -First 1
if (-not $folder) { throw "JDK archive did not contain a directory" }
Remove-Item -Recurse -Force $targetPath -ErrorAction SilentlyContinue
Move-Item $folder.FullName $targetPath
Remove-Item -Recurse -Force $temp
Remove-Item -Force $archive
if (-not (Test-Java17 $java)) { throw "Downloaded runtime is not JDK 17" }
Write-Output $targetPath
