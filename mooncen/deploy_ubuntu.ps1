param(
    [string]$Server = "",
    [string]$SshHost = "",

    [string]$User = "ubuntu",
    [string]$IdentityFile = "",
    [string]$RemoteDir = "/opt/mooncen",
    [string]$Domain = "_",
    [string]$DbPassword = "",
    [string]$DbApiPassword = "",
    [string]$DbCrawlerPassword = "",
    [string]$DbAiPassword = "",
    [string]$DbApplierPassword = "",
    [string]$DbBackupPassword = "",
    [string]$DbCheckPassword = "",
    [string]$AuthSecret = "",
    [string]$OpsLoginId = "",
    [string]$OpsPasswordHash = "",
    [string]$DbSslRootCert = "",
    [string]$BackupAgeRecipient = "",
    [string]$BackupPort = "",
    [string]$KakaoMapsJavascriptKey = "",
    [string]$KakaoMapsRestApiKey = "",
    [string]$GoogleOAuthClientId = "",
    [string]$GoogleOAuthClientSecret = "",
    [string]$NaverOAuthClientId = "",
    [string]$NaverOAuthClientSecret = "",
    [string]$CloudflaredToken = "",
    [string]$OllamaHost = "http://wtr-linux:11434",
    [string]$OllamaHosts = "",
    [string]$OllamaModel = "qwen3.5:9b",
    [string]$BotToken = "",
    [string]$BotChatId = "",
    [string]$AdminEmails = "",
    [string]$AdminProviderIds = "",
    [string]$BugReportTo = "",
    [string]$BugReportFrom = "",
    [string]$SmtpHost = "",
    [string]$SmtpPort = "",
    [string]$SmtpUsername = "",
    [string]$SmtpPassword = "",
    [ValidateSet("starttls", "ssl", "none", "")]
    [string]$SmtpSecurity = "",
    [string]$OpsCloudflareAnalyticsZoneId = "",
    [string]$OpsCloudflareAnalyticsToken = "",
    [string]$ServerMonitorToken = "",
    [string]$ExpectedCommit = "",
    [string]$DeploymentIntentToken = "",
    [string]$SourceCommit = "",
    [string]$ExpectedSourceTree = "",
    [ValidateSet("primary", "standby", "")]
    [string]$NodeRole = "",
    [ValidateSet("legacy", "distributed", "")]
    [string]$CrawlerMode = "",
    [switch]$EnableCrawler,
    [switch]$SkipSystemPackages,
    [switch]$SkipWorkers,
    [switch]$UseScpFallback,
    [switch]$InstallOpsConsole,
    [switch]$AllowCrawlerInterruption,
    [switch]$Standby
)

$targetHost = if ($SshHost) { $SshHost } else { $Server }
if (-not $targetHost) {
    throw "Set -Server or -SshHost. You can use either an IP address or a domain."
}

# Ops Console has a separate release path; never install it with the public
# MoonCen deployment even when a legacy caller still passes this switch.
$InstallOpsConsole = $false

$script = Join-Path $PSScriptRoot "deploy/ubuntu/deploy_from_windows.ps1"
& $script `
    -SshHost $targetHost `
    -User $User `
    -IdentityFile $IdentityFile `
    -RemoteDir $RemoteDir `
    -Domain $Domain `
    -DbPassword $DbPassword `
    -DbApiPassword $DbApiPassword `
    -DbCrawlerPassword $DbCrawlerPassword `
    -DbAiPassword $DbAiPassword `
    -DbApplierPassword $DbApplierPassword `
    -DbBackupPassword $DbBackupPassword `
    -DbCheckPassword $DbCheckPassword `
    -AuthSecret $AuthSecret `
    -OpsLoginId $OpsLoginId `
    -OpsPasswordHash $OpsPasswordHash `
    -DbSslRootCert $DbSslRootCert `
    -BackupAgeRecipient $BackupAgeRecipient `
    -BackupPort $BackupPort `
    -KakaoMapsJavascriptKey $KakaoMapsJavascriptKey `
    -KakaoMapsRestApiKey $KakaoMapsRestApiKey `
    -GoogleOAuthClientId $GoogleOAuthClientId `
    -GoogleOAuthClientSecret $GoogleOAuthClientSecret `
    -NaverOAuthClientId $NaverOAuthClientId `
    -NaverOAuthClientSecret $NaverOAuthClientSecret `
    -CloudflaredToken $CloudflaredToken `
    -OllamaHost $OllamaHost `
    -OllamaHosts $OllamaHosts `
    -OllamaModel $OllamaModel `
    -BotToken $BotToken `
    -BotChatId $BotChatId `
    -AdminEmails $AdminEmails `
    -AdminProviderIds $AdminProviderIds `
    -BugReportTo $BugReportTo `
    -BugReportFrom $BugReportFrom `
    -SmtpHost $SmtpHost `
    -SmtpPort $SmtpPort `
    -SmtpUsername $SmtpUsername `
    -SmtpPassword $SmtpPassword `
    -SmtpSecurity $SmtpSecurity `
    -OpsCloudflareAnalyticsZoneId $OpsCloudflareAnalyticsZoneId `
    -OpsCloudflareAnalyticsToken $OpsCloudflareAnalyticsToken `
    -ServerMonitorToken $ServerMonitorToken `
    -ExpectedCommit $ExpectedCommit `
    -DeploymentIntentToken $DeploymentIntentToken `
    -SourceCommit $SourceCommit `
    -ExpectedSourceTree $ExpectedSourceTree `
    -NodeRole $NodeRole `
    -CrawlerMode $CrawlerMode `
    -EnableCrawler:$EnableCrawler `
    -InstallOpsConsole:$InstallOpsConsole `
    -SkipSystemPackages:$SkipSystemPackages `
    -SkipWorkers:$SkipWorkers `
    -UseScpFallback:$UseScpFallback `
    -AllowCrawlerInterruption:$AllowCrawlerInterruption `
    -Standby:$Standby
$deploymentExitCode = $LASTEXITCODE
exit $deploymentExitCode
