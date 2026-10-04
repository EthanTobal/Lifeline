# Redeploy the Lifeline backend to AWS Lambda.
#
# Repackages backend/app, uploads to S3, and updates the existing Lambda
# function's code. The IAM role, API Gateway, and env vars already exist —
# this only pushes new code. Run from anywhere:
#
#     powershell -ExecutionPolicy Bypass -File backend\deploy.ps1
#
# Requires: AWS CLI logged in as the 'lifeline' profile (aws login).

$ErrorActionPreference = 'Stop'

$Profile  = 'lifeline'
$Region   = 'us-east-2'
$Function = 'lifeline-backend'
$Bucket   = 'lifeline-project-data-714047902595'
$Key      = 'deploy/lambda_pkg.zip'

$backend = Split-Path -Parent $MyInvocation.MyCommand.Path
$app     = Join-Path $backend 'app'
$zip     = Join-Path $backend 'lambda_pkg.zip'

Write-Host '==> Packaging backend/app ...'
# Build the zip with forward-slash paths (Lambda needs them) via Python.
$py = @"
import zipfile, os
root = r'$backend'
app = os.path.join(root, 'app')
zp = os.path.join(root, 'lambda_pkg.zip')
if os.path.exists(zp):
    os.remove(zp)
z = zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED)
# 1. The application code (app/*.py).
for d, _, fs in os.walk(app):
    if '__pycache__' in d:
        continue
    for f in fs:
        if f.endswith('.py'):
            full = os.path.join(d, f)
            z.write(full, os.path.relpath(full, root).replace(os.sep, '/'))
# 2. The demo data JSON (backend/data/*.json). The product matcher's catalog
#    fallback and the policy-record lookup read these at runtime; without them
#    in the package, those features go inert in Lambda. Shipped at 'data/' so
#    the modules' _BACKEND_DIR/data path resolves (zip root is the function
#    root, i.e. the parent of app/).
data = os.path.join(root, 'data')
if os.path.isdir(data):
    for d, _, fs in os.walk(data):
        for f in fs:
            if f.endswith('.json'):
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, root).replace(os.sep, '/'))
# 3. The policy service (services/*.py) the matcher prefers over the fallback.
services = os.path.join(root, 'services')
if os.path.isdir(services):
    for d, _, fs in os.walk(services):
        if '__pycache__' in d:
            continue
        for f in fs:
            if f.endswith('.py'):
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, root).replace(os.sep, '/'))
z.close()
names = zipfile.ZipFile(zp).namelist()
print('packaged', os.path.getsize(zp), 'bytes,', len(names), 'files')
print('  data:', [n for n in names if n.startswith('data/')])
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
