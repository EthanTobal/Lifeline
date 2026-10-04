# Redeploy the Lifeline backend to AWS Lambda.
#
# Packages backend/index.mjs only (tests stay out of the zip) and updates
# Lifeline-ModelRequest. Values come from the environment or backend/.env.
# Nothing account-specific is stored in this script.
#
#     powershell -ExecutionPolicy Bypass -File backend\deploy.ps1

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

$backend = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-LifelineEnv (Join-Path $backend '.env')

$Profile  = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { 'lifeline' }
$Region   = if ($env:AWS_REGION) { $env:AWS_REGION } else { 'us-east-2' }
$Function = if ($env:LIFELINE_FUNCTION) { $env:LIFELINE_FUNCTION } else { 'Lifeline-ModelRequest' }
$Bucket   = Require-Setting 'LIFELINE_BUCKET'
$Key      = 'deploy/lifeline-model.zip'
$zip      = Join-Path $backend 'lambda_pkg.zip'

Write-Host '==> Packaging backend/index.mjs ...'
$py = @"
import zipfile, os
root = r'$backend'
zp = os.path.join(root, 'lambda_pkg.zip')
if os.path.exists(zp):
    os.remove(zp)
z = zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED)
z.write(os.path.join(root, 'index.mjs'), 'index.mjs')
z.close()
print('packaged', os.path.getsize(zp), 'bytes')
"@
$py | py -

Write-Host '==> Uploading to S3 ...'
aws s3 cp $zip "s3://$Bucket/$Key" --profile $Profile --region $Region | Out-Null

Write-Host '==> Updating Lambda code ...'
aws lambda update-function-code `
    --function-name $Function `
    --s3-bucket $Bucket `
    --s3-key $Key `
    --profile $Profile --region $Region `
    --query 'LastUpdateStatus' --output text

Write-Host '==> Done.'
if ($env:LIFELINE_API_BASE) {
    Write-Host "    $($env:LIFELINE_API_BASE.TrimEnd('/'))/health"
}
Write-Host 'Set SESSION_SECRET, DOCUMENT_BUCKET, BEDROCK_KNOWLEDGE_BASE_ID, CORS_ORIGINS, and ASSESSMENTS_TABLE on the function. This script does not overwrite Lambda environment variables.'
