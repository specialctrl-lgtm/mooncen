$MoonCenCloudServer = "cloud"
$MoonCenCloudUser = "ubuntu"
$MoonCenCloudDomain = "mooncen.kr"
$MoonCenCloudRemoteDir = "/opt/mooncen"

# Optional but recommended after creating a passphrase-free deploy key.
$MoonCenCloudIdentityFile = "ssh-agent"

$MoonCenKakaoMapsJavascriptKey = ""
$MoonCenKakaoMapsRestApiKey = ""
$MoonCenGoogleOAuthClientId = ""
$MoonCenGoogleOAuthClientSecret = ""
$MoonCenNaverOAuthClientId = ""
$MoonCenNaverOAuthClientSecret = ""
$MoonCenOllamaHost = "http://wtr-linux:11434"
$MoonCenOllamaHosts = "http://wtr-linux:11434,http://victus:11434"
$MoonCenOllamaModel = "qwen3.5:9b"
# Optional local Ops Console probe list.
$env:OLLAMA_HOSTS = $MoonCenOllamaHosts

# Telegram Ops Bot. Keep the token private in deploy.local.ps1.
# The bot is not enabled on the current cloud-only production topology.
$MoonCenBotToken = ""
$MoonCenBotChatId = ""

# API-only administrator allowlists. Prefer immutable provider identities such
# as "google:123456789,naver:abcdef"; email entries require verified OAuth mail.
$MoonCenAdminEmails = ""
$MoonCenAdminProviderIds = ""

# Bug report email delivery. Keep real addresses and SMTP credentials only in
# deploy.local.ps1 or the protected remote API environment.
$MoonCenBugReportTo = ""
$MoonCenBugReportFrom = ""
$MoonCenSmtpHost = ""
$MoonCenSmtpPort = "587"
$MoonCenSmtpUsername = ""
$MoonCenSmtpPassword = ""
# starttls (recommended), ssl, or none (development/internal testing only).
$MoonCenSmtpSecurity = "starttls"

# Optional server-side visitor metrics in the Ops Console. Follow Cloudflare's
# current GraphQL Analytics token guide, grant read-only analytics access, and
# resource-scope the token to the MoonCen zone; never expose it to VITE_*.
$MoonCenOpsCloudflareAnalyticsZoneId = ""
$MoonCenOpsCloudflareAnalyticsToken = ""

# Dedicated machine credential for the BOT monitor's read-only production
# quality snapshot. Generate a separate URL-safe random value; never reuse the
# APK token, an Ops password, or a browser session token.
$MoonCenServerMonitorToken = ""

# Required for the dedicated Ops Console login. Generate the hash with
# .\venv_clean\Scripts\python.exe tools\generate_ops_password.py and keep it
# only in deploy.local.ps1 or the protected remote deploy-secret store.
$MoonCenOpsLoginId = "opsadmin"
$MoonCenOpsPasswordHash = ""

# Optional. Use one Cloudflare Tunnel token for every MoonCen node.
# The role guard starts cloudflared only on the active primary.
$MoonCenCloudflaredToken = ""

# Optional. If empty, deploy reuses values from the protected remote app,
# backup, or deploy-secret environment files.
# Set these only when you intentionally want to rotate secrets.
$MoonCenDbPassword = ""
$MoonCenDbApiPassword = ""
$MoonCenDbCrawlerPassword = ""
$MoonCenDbAiPassword = ""
$MoonCenDbApplierPassword = ""
$MoonCenDbBackupPassword = ""
$MoonCenDbCheckPassword = ""
$MoonCenAuthSecret = ""

# Absolute path on the remote server to a pre-provisioned PostgreSQL CA file.
# Required when PRIMARY_DB_HOST is remote; setup copies it into /etc/mooncen.
$MoonCenDbSslRootCert = ""

# Required on the primary for encrypted backups. This is a public age X25519
# recipient (age1...), not the private identity key.
$MoonCenBackupAgeRecipient = ""
# Leave empty for SSH port 22. A non-standard NAS SSH port is pinned as
# `[host]:port` in /etc/mooncen/backup-known-hosts.
$MoonCenBackupPort = ""

# After the first full deploy, leave this true for faster redeploys.
$MoonCenSkipSystemPackages = $true
$MoonCenUseScpFallback = $false

# Required for the current server layout:
# Copy config/deploy_servers.example.json to config/deploy_servers.json and keep
# cloud as the sole role=primary/active=true server and crawler owner.
# deploy_mooncen.ps1 defaults to a single deploy against the configured
# defaultTarget. Keep defaultTarget on cloud for the active production topology;
# should be applied through Ops Console unless explicitly intended otherwise.
