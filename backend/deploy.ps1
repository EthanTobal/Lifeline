# Redeploy the Lifeline backend to AWS Lambda.
#
# Packages backend/index.mjs and updates Lifeline-ModelRequest, the function
# the live API Gateway route calls. Run from anywhere:
#
#     powershell -ExecutionPolicy Bypass -File backend\deploy.ps1
#
# Requires: AWS CLI logged in as the 'lifeline' profile (aws login).

$ErrorActionPreference = 'Stop'

$Profile  = 'lifeline'
$Region   = 'us-east-2'
$Function = 'Lifeline-ModelRequest'
$Bucket   = 'lifeline-project-data-714047902595'
$Key      = 'deploy/lifeline-model.zip'

$backend = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip     = Join-Path $backend 'lambda_pkg.zip'

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

Write-Host '==> Done. Give it a few seconds, then test:'
Write-Host '    https://oa8m1sol3h.execute-api.us-east-2.amazonaws.com/health'
