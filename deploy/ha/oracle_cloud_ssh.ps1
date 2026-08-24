param(
    [Parameter(Mandatory=$true)]
    [string]$Server,
    [string]$User = "ubuntu",
    [string]$KeyPath = ".\oracle_cloud_mooncen.key",
    [string]$Command = "hostname && uname -a"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $KeyPath)) {
    throw "Key not found: $KeyPath"
}

$resolvedKey = (Resolve-Path -LiteralPath $KeyPath).Path
ssh -i $resolvedKey -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no "$User@$Server" $Command
