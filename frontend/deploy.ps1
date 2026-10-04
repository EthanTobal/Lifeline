# Redeploy the Lifeline frontend to AWS.
#
# Syncs frontend/ to the web/ prefix and invalidates CloudFront.
# Bucket and distribution id come from the environment or backend/.env.
#
#     powershell -ExecutionPolicy Bypass -File frontend\deploy.ps1

$ErrorActionPreference = 'Stop'

function Import-LifelineEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $name, $value = $line -split '=', 2
        $name = $name.Trim()
        if (-not $name) { continue }
        $value = $value.Trim().Trim('"').Trim("'")
        if (-not [Environment]::GetEnvironmentVariable($name)) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Require-Setting([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value) { throw "Set $Name in the environment or backend/.env" }
    return $value
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-LifelineEnv (Join-Path (Split-Path $here) 'backend/.env')

$Profile        = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { 'lifeline' }
$Region         = if ($env:AWS_REGION) { $env:AWS_REGION } else { 'us-east-2' }
$Bucket         = Require-Setting 'LIFELINE_BUCKET'
$Prefix         = 'web'
$DistributionId = Require-Setting 'LIFELINE_DISTRIBUTION_ID'

Write-Host "==> Syncing frontend/ to s3://$Bucket/$Prefix/ ..."
# --delete is intentionally NOT used: the bucket also holds archived versions
# under web/archive/ that are not part of this working tree.
aws s3 sync $here "s3://$Bucket/$Prefix/" `
    --exclude '.gitkeep' `
    --exclude 'deploy.ps1' `
    --exclude 'BRANDING.md' `
    --exclude 'config.example.js' `
    --exclude 'archive/*' `
    --exclude 'archive/*/*' `
    --exclude 'archive/*/*/*' `
    --profile $Profile --region $Region

Write-Host '==> Invalidating CloudFront ...'
$invId = aws cloudfront create-invalidation `
    --distribution-id $DistributionId `
    --paths '/*' `
    --profile $Profile `
    --query 'Invalidation.Id' --output text
Write-Host "    invalidation: $invId"
Write-Host '==> Done.'
