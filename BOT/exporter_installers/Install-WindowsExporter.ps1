[CmdletBinding()]
param(
    [string]$ListenAddress = "",

    [ValidateRange(1, 65535)]
    [int]$Port = 9182,

    [string]$AllowedRemoteAddress = "Any",

    [string]$Collectors = "[defaults]",

    [switch]$AllowAnyListen,

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ExporterVersion = "0.31.7"
$Sha256ByArchitecture = @{
    amd64 = "D29114FB7AE6AF2865A9F1C4BE7FDCCC2B9FBBB4366A811B620BC85C983D0007"
    arm64 = "7793B655CD7A3FEB621F67AC48E5A2D409F8EC65C396BD3CAC571278D48685AF"
}
$HardwareMonitorVersion = "0.9.6"
$HardwareMonitorSha256 = "086D9F1B5A99E643EDC2CFAAAC16051685B551E4C5AC0B32A57C58C0E529C001"
$FirewallRuleName = "MoonCen-WindowsExporter"
$TemperatureTaskName = "MoonCen Hardware Temperature Collector"
$TemperatureInstallDirectory = Join-Path $env:ProgramFiles "MoonCenTemperature"
$TemperatureCollectorPath = Join-Path $TemperatureInstallDirectory "Collect-WindowsTemperature.ps1"
$TemperatureTextfileDirectory = Join-Path $env:ProgramData "windows_exporter\textfile_inputs"
$TemperatureMetricPath = Join-Path $TemperatureTextfileDirectory "mooncen_temperature.prom"
$TemperatureCollectorSource = @'
param(
    [Parameter(Mandatory = $true)]
    [string]$LibraryDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$computer = $null
$exitCode = 0
$temperatureMetrics = New-Object "System.Collections.Generic.List[string]"

function ConvertTo-PrometheusLabel {
    param([AllowEmptyString()][string]$Value)

    if ($null -eq $Value) {
        return ""
    }
    return $Value.Replace('\', '\\').Replace("`r", '\r').Replace("`n", '\n').Replace('"', '\"')
}

function Read-HardwareTemperature {
    param([Parameter(Mandatory = $true)][object]$Hardware)

    $Hardware.Update()
    foreach ($sensor in @($Hardware.Sensors)) {
        if (
            $sensor.SensorType -ne [LibreHardwareMonitor.Hardware.SensorType]::Temperature -or
            $null -eq $sensor.Value
        ) {
            continue
        }

        $value = [double]$sensor.Value
        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) {
            continue
        }

        $hardwareType = ConvertTo-PrometheusLabel $Hardware.HardwareType.ToString()
        $hardwareName = ConvertTo-PrometheusLabel ([string]$Hardware.Name)
        $sensorName = ConvertTo-PrometheusLabel ([string]$sensor.Name)
        $formattedValue = $value.ToString(
            "0.###",
            [Globalization.CultureInfo]::InvariantCulture
        )
        $temperatureMetrics.Add(
            "mooncen_hardware_temperature_celsius{hardware_type=`"$hardwareType`",hardware=`"$hardwareName`",sensor=`"$sensorName`"} $formattedValue"
        )
    }

    foreach ($subHardware in @($Hardware.SubHardware)) {
        Read-HardwareTemperature -Hardware $subHardware
    }
}

try {
    $libraryPath = Join-Path $LibraryDirectory "LibreHardwareMonitorLib.dll"
    if (-not (Test-Path -LiteralPath $libraryPath -PathType Leaf)) {
        throw "LibreHardwareMonitorLib.dll is missing."
    }

    Push-Location $LibraryDirectory
    try {
        Add-Type -Path $libraryPath
        $computer = New-Object LibreHardwareMonitor.Hardware.Computer
        $computer.IsCpuEnabled = $true
        $computer.IsGpuEnabled = $true
        $computer.IsMemoryEnabled = $true
        $computer.IsMotherboardEnabled = $true
        $computer.IsStorageEnabled = $true
        $computer.IsControllerEnabled = $true
        $computer.Open()

        foreach ($hardware in @($computer.Hardware)) {
            Read-HardwareTemperature -Hardware $hardware
        }
    } finally {
        Pop-Location
    }
} catch {
    $exitCode = 1
} finally {
    if ($null -ne $computer) {
        try {
            $computer.Close()
        } catch {
            $exitCode = 1
        }
    }
}

$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$lines = New-Object "System.Collections.Generic.List[string]"
$lines.Add("# HELP mooncen_hardware_temperature_celsius Hardware sensor temperature in Celsius.")
$lines.Add("# TYPE mooncen_hardware_temperature_celsius gauge")
foreach ($metric in $temperatureMetrics) {
    $lines.Add($metric)
}
$lines.Add("# HELP mooncen_temperature_sensor_count Number of readable temperature sensors.")
$lines.Add("# TYPE mooncen_temperature_sensor_count gauge")
$lines.Add("mooncen_temperature_sensor_count $($temperatureMetrics.Count)")
$lines.Add("# HELP mooncen_temperature_collector_success Whether LibreHardwareMonitor collection succeeded.")
$lines.Add("# TYPE mooncen_temperature_collector_success gauge")
$lines.Add("mooncen_temperature_collector_success $([int]($exitCode -eq 0))")
$lines.Add("# HELP mooncen_temperature_collector_timestamp_seconds Unix time of the latest collection attempt.")
$lines.Add("# TYPE mooncen_temperature_collector_timestamp_seconds gauge")
$lines.Add("mooncen_temperature_collector_timestamp_seconds $timestamp")

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$temporaryOutput = "$OutputPath.$PID.tmp"
try {
    [IO.File]::WriteAllLines(
        $temporaryOutput,
        $lines,
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryOutput -Destination $OutputPath -Force
} finally {
    if (Test-Path -LiteralPath $temporaryOutput) {
        Remove-Item -LiteralPath $temporaryOutput -Force
    }
}

exit $exitCode
'@

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-TailscaleIPv4 {
    $command = Get-Command "tailscale.exe" -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $null
    }

    $candidate = @(& $command.Source ip -4 2>$null) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return $null
    }

    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($candidate.Trim(), [ref]$parsed)) {
        return $null
    }
    if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        return $null
    }
    return $parsed.ToString()
}

$nativeArchitecture = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
} else {
    $env:PROCESSOR_ARCHITECTURE
}
switch ($nativeArchitecture.ToUpperInvariant()) {
    "AMD64" { $releaseArchitecture = "amd64" }
    "ARM64" { $releaseArchitecture = "arm64" }
    default { throw "Unsupported Windows architecture: $nativeArchitecture" }
}

if ([string]::IsNullOrWhiteSpace($ListenAddress)) {
    $ListenAddress = Get-TailscaleIPv4
}
if ([string]::IsNullOrWhiteSpace($ListenAddress)) {
    $ListenAddress = "127.0.0.1"
    Write-Warning "Tailscale IPv4 was not found; binding to loopback only."
    Write-Warning "Rerun with -ListenAddress PRIVATE_IP for remote scraping."
}

$parsedListenAddress = $null
if (-not [Net.IPAddress]::TryParse($ListenAddress, [ref]$parsedListenAddress)) {
    throw "ListenAddress must be an IPv4 address."
}
if ($parsedListenAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
    throw "Only IPv4 listen addresses are supported by this installer."
}
$ListenAddress = $parsedListenAddress.ToString()
if ($ListenAddress -eq "0.0.0.0" -and -not $AllowAnyListen) {
    throw "Refusing 0.0.0.0 binding without -AllowAnyListen."
}
if ([string]::IsNullOrWhiteSpace($Collectors) -or $Collectors -match "\s") {
    throw "Collectors must be a non-empty comma-separated value without spaces."
}
if ([string]::IsNullOrWhiteSpace($AllowedRemoteAddress)) {
    throw "AllowedRemoteAddress cannot be empty."
}

$assetName = "windows_exporter-$ExporterVersion-$releaseArchitecture.msi"
$downloadUrl = "https://github.com/prometheus-community/windows_exporter/releases/download/v$ExporterVersion/$assetName"
$hardwareMonitorAssetName = "LibreHardwareMonitor.zip"
$hardwareMonitorDownloadUrl = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v$HardwareMonitorVersion/$hardwareMonitorAssetName"
$temporaryDirectory = Join-Path $env:TEMP ("mooncen-windows-exporter-" + [Guid]::NewGuid().ToString("N"))
$msiPath = Join-Path $temporaryDirectory $assetName
$hardwareMonitorArchivePath = Join-Path $temporaryDirectory $hardwareMonitorAssetName
$hardwareMonitorExtractPath = Join-Path $temporaryDirectory "LibreHardwareMonitor"
$logPath = Join-Path $temporaryDirectory "msiexec.log"
$installationSucceeded = $false

New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $msiPath
    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri $hardwareMonitorDownloadUrl `
        -OutFile $hardwareMonitorArchivePath

    $actualHash = (Get-FileHash -LiteralPath $msiPath -Algorithm SHA256).Hash
    $expectedHash = $Sha256ByArchitecture[$releaseArchitecture]
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 verification failed for $assetName."
    }
    $hardwareMonitorActualHash = (
        Get-FileHash -LiteralPath $hardwareMonitorArchivePath -Algorithm SHA256
    ).Hash
    if ($hardwareMonitorActualHash -ne $HardwareMonitorSha256) {
        throw "SHA-256 verification failed for $hardwareMonitorAssetName."
    }

    Expand-Archive `
        -LiteralPath $hardwareMonitorArchivePath `
        -DestinationPath $hardwareMonitorExtractPath
    $hardwareMonitorLibrary = Get-ChildItem `
        -LiteralPath $hardwareMonitorExtractPath `
        -Filter "LibreHardwareMonitorLib.dll" `
        -File `
        -Recurse |
        Select-Object -First 1
    if ($null -eq $hardwareMonitorLibrary) {
        throw "LibreHardwareMonitorLib.dll is missing from the verified archive."
    }
    $hardwareMonitorSourceDirectory = $hardwareMonitorLibrary.Directory.FullName

    if ($ValidateOnly) {
        $installationSucceeded = $true
        Write-Output "validation=ok"
        Write-Output "asset=$assetName"
        Write-Output "sha256=$actualHash"
        Write-Output "temperature_asset=$hardwareMonitorAssetName"
        Write-Output "temperature_sha256=$hardwareMonitorActualHash"
        return
    }

    if (-not (Test-Administrator)) {
        throw "Run this installer from an elevated PowerShell window."
    }

    New-Item -ItemType Directory -Path $TemperatureInstallDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $TemperatureTextfileDirectory -Force | Out-Null
    Get-ChildItem -LiteralPath $hardwareMonitorSourceDirectory -Force |
        Copy-Item -Destination $TemperatureInstallDirectory -Recurse -Force
    [IO.File]::WriteAllText(
        $TemperatureCollectorPath,
        $TemperatureCollectorSource,
        [Text.UTF8Encoding]::new($false)
    )

    $powerShellExecutable = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    & $powerShellExecutable `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy Bypass `
        -File $TemperatureCollectorPath `
        -LibraryDirectory $TemperatureInstallDirectory `
        -OutputPath $TemperatureMetricPath
    if ($LASTEXITCODE -ne 0) {
        throw "LibreHardwareMonitor temperature collection failed."
    }

    $taskAction = New-ScheduledTaskAction `
        -Execute $powerShellExecutable `
        -Argument (
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass " +
            "-File `"$TemperatureCollectorPath`" " +
            "-LibraryDirectory `"$TemperatureInstallDirectory`" " +
            "-OutputPath `"$TemperatureMetricPath`""
        )
    $startupTrigger = New-ScheduledTaskTrigger -AtStartup
    $minuteTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $taskPrincipal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 50) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName $TemperatureTaskName `
        -Action $taskAction `
        -Trigger @($startupTrigger, $minuteTrigger) `
        -Principal $taskPrincipal `
        -Settings $taskSettings `
        -Force | Out-Null

    $collectorList = @($Collectors.Split(","))
    $effectiveCollectors = if ($collectorList -contains "textfile") {
        $Collectors
    } else {
        "$Collectors,textfile"
    }
    $msiArguments = @(
        "/i"
        "`"$msiPath`""
        "/qn"
        "/norestart"
        "LISTEN_ADDR=$ListenAddress"
        "LISTEN_PORT=$Port"
        "ENABLED_COLLECTORS=$effectiveCollectors"
        "TEXTFILE_DIRS=`"$TemperatureTextfileDirectory`""
        "REMOVE=FirewallException"
        "/L*v"
        "`"$logPath`""
    )
    $installedExporterPath = Join-Path $env:ProgramFiles "windows_exporter\windows_exporter.exe"
    if (Test-Path -LiteralPath $installedExporterPath -PathType Leaf) {
        $installedVersion = (Get-Item -LiteralPath $installedExporterPath).VersionInfo.ProductVersion
        if ($installedVersion -like "$ExporterVersion*") {
            $msiArguments += "REINSTALL=ALL"
            $msiArguments += "REINSTALLMODE=vomus"
        }
    }
    $installer = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArguments -Wait -PassThru
    if ($installer.ExitCode -notin @(0, 3010)) {
        throw "MSI installation failed with exit code $($installer.ExitCode). Log: $logPath"
    }

    $service = Get-Service -Name "windows_exporter" -ErrorAction Stop
    Set-Service -Name "windows_exporter" -StartupType Automatic
    if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Running) {
        Start-Service -Name "windows_exporter"
    } else {
        Restart-Service -Name "windows_exporter" -Force
    }

    $existingRule = Get-NetFirewallRule -Name $FirewallRuleName -ErrorAction SilentlyContinue
    if ($null -ne $existingRule) {
        Remove-NetFirewallRule -Name $FirewallRuleName
    }
    if ($ListenAddress -ne "127.0.0.1") {
        $firewallLocalAddress = if ($ListenAddress -eq "0.0.0.0") {
            "Any"
        } else {
            $ListenAddress
        }
        New-NetFirewallRule `
            -Name $FirewallRuleName `
            -DisplayName "MoonCen Windows Exporter" `
            -Description "Allow Prometheus scraping on the selected private address only." `
            -Direction Inbound `
            -Action Allow `
            -Enabled True `
            -Profile Any `
            -Protocol TCP `
            -LocalAddress $firewallLocalAddress `
            -LocalPort $Port `
            -RemoteAddress $AllowedRemoteAddress | Out-Null
    }

    $metricsUrl = "http://${ListenAddress}:$Port/metrics"
    $ready = $false
    foreach ($attempt in 1..10) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $metricsUrl -TimeoutSec 2
            if (
                $response.StatusCode -eq 200 -and
                $response.Content -match "windows_exporter_build_info" -and
                $response.Content -match "mooncen_temperature_collector_success"
            ) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        throw "windows_exporter is installed but the metrics probe failed: $metricsUrl"
    }

    $installationSucceeded = $true
    Write-Output "installation=ok"
    Write-Output "service=windows_exporter"
    Write-Output "version=$ExporterVersion"
    Write-Output "listen_address=${ListenAddress}:$Port"
    Write-Output "metrics_url=$metricsUrl"
    Write-Output "temperature_library=LibreHardwareMonitor-$HardwareMonitorVersion"
    $temperatureCountMatch = Select-String `
        -LiteralPath $TemperatureMetricPath `
        -Pattern "^mooncen_temperature_sensor_count ([0-9]+)$" |
        Select-Object -First 1
    $temperatureSensorCount = if ($null -eq $temperatureCountMatch) {
        0
    } else {
        [int]$temperatureCountMatch.Matches[0].Groups[1].Value
    }
    Write-Output "temperature_sensor_count=$temperatureSensorCount"
} finally {
    if ($installationSucceeded -and (Test-Path -LiteralPath $temporaryDirectory)) {
        $resolvedTemporaryDirectory = [IO.Path]::GetFullPath($temporaryDirectory)
        $resolvedTempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if (
            $resolvedTemporaryDirectory.StartsWith(
                $resolvedTempRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [IO.Path]::GetFileName($resolvedTemporaryDirectory).StartsWith(
                "mooncen-windows-exporter-",
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            Remove-Item -LiteralPath $resolvedTemporaryDirectory -Recurse -Force
        }
    } elseif (-not $installationSucceeded) {
        Write-Warning "Installation diagnostics were retained in: $temporaryDirectory"
    }
}
