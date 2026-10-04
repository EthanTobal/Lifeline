# Redeploy the Lifeline frontend to AWS.
#
# Syncs frontend/ to the 'web/' prefix of the project bucket (where the site
# assets are served from) and invalidates the CloudFront distribution so the
# new files are served immediately. The bucket, distribution, and all other
# infrastructure already exist -- this only pushes new static assets. Run from
# anywhere:
#
#     powershell -ExecutionPolicy Bypass -File frontend\deploy.ps1
#
# Requires: AWS CLI logged in as the 'lifeline' profile (aws login).

$ErrorActionPreference = 'Stop'

$Profile        = 'lifeline'
$Region         = 'us-east-2'
$Bucket         = 'lifeline-project-data-714047902595'
$Prefix         = 'web'                       # site assets live under s3://<bucket>/web/
$DistributionId = 'E39RCZK1I85R9U'            # CloudFront distribution for the site

$frontend = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==> Syncing frontend/ to s3://$Bucket/$Prefix/ ..."
# --delete is intentionally NOT used: the bucket also holds archived versions
# under web/archive/ that are not part of this working tree. .gitkeep is a repo
# marker that does not belong in the deployed site.
aws s3 sync $frontend "s3://$Bucket/$Prefix/" `
    --exclude '.gitkeep' `
    --exclude 'deploy.ps1' `
    --exclude 'BRANDING.md' `
    --profile $Profile --region $Region

Write-Host '==> Invalidating CloudFront ...'
$invId = aws cloudfront create-invalidation `
    --distribution-id $DistributionId `
    --paths '/*' `
    --profile $Profile `
    --query 'Invalidation.Id' --output text
Write-Host "    invalidation: $invId"

Write-Host '==> Done. The site will refresh once the invalidation completes:'
Write-Host '    https://d3rlyqanqecvz3.cloudfront.net/'
