# AWS EC2 deploy kit — JAVSTREAMIO debrid-app

Hosts the live debrid-app on an always-on EC2 in us-east-1: free-tier
`t2.micro` (12 months free) or `t3.small` funded by the new-account $ credit.
Heavy media (Hentaistream mp4, DMM/GitHub-raw posters) is fetched directly by
Stremio, so EC2 egress stays tiny (small addon JSON only).

Files:
- `cloudformation.yaml` — SG + EC2 (with inline user-data) + Elastic IP. Lint-clean (cfn-lint).
- `user-data.sh` — bootstrap: nginx + Node 24 + pm2 + clone repo, build, start.
- `deploy.sh` — re-deploy on the box after each `git push`.
- `pm2.ecosystem.json` — pm2 app config.
- `nginx.javstremio.conf` — nginx site (proxy 80/443 → 127.0.0.1:3000).

## 1. Find a current Ubuntu 24.04 AMI (us-east-1)
AWS console → EC2 → AMIs → filters: owner "Amazon", name "ubuntu",
name contains "hvm-ubuntu-noble" (24.04). Copy the `ami-...` id.
Verify the CFN default `ami-0c03d16f24160d2a6` is still valid, or pass a
current `AmzId` at stack creation.

## 2. Get your default-VPC ids
VPC console → your default VPC → copy the `vpc-...` id, and from its subnets
copy one `sub-...` id (us-east-1a or 1b).

## 3. Create the stack
```bash
aws cloudformation create-stack \
  --stack-name javstremio \
  --template-body file://cloudformation.yaml \
  --parameters \
    ParameterKey=Domain,ParameterValue= \
    ParameterKey=InstanceType,ParameterValue=t2.micro \
    ParameterKey=SubnetId,ParameterValue=sub-XXXXXXXX \
    ParameterKey=KeyName,ParameterValue= \
    ParameterKey=VpcId,ParameterValue=vpc-XXXXXXXX \
    ParameterKey=AmzId,ParameterValue=ami-XXXXXXXX
```
`Domain` and `KeyName` are optional (empty default). If you have a domain,
pass it — user-data installs certbot and auto-provisions Let's Encrypt.

## 4. Verify live
```bash
curl -s http://<stack-output-PublicIp>/manifest.json | head
```
Then in Stremio: Addon URL = `http://<ip>/manifest.json` (or
`https://<domain>/manifest.json`). hnt rows with `stream_url` (65 rows as of
2026-11-06) give direct Hentaistream mp4 playback.

## 5. Re-deploy after each push
From the EC2 box (or scp):
```bash
DOMAIN=your-domain-or-ip ./deploy.sh
```
It does `git pull --ff-only`, `npm ci`, `npm run build`, `pm2 restart`,
`systemctl reload nginx`.

## Cost notes
- `t2.micro`: 12 months free (750 h/mo) on a new account.
- Data transfer: only small JSON responses leave the box; the 100 GB/mo free
  egress tier covers it easily.
- After 12 months or if `t2.micro` is too slow, switch the stack parameter to
  `t3.small` and pay with the new-account credit.
- EIP is free while attached to a running instance.
