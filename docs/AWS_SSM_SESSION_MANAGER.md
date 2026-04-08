# AWS Systems Manager Session Manager (this instance)

Use **Session Manager** to get a shell over **HTTPS to AWS APIs** (no inbound SSH required on security groups).

## Instance reference

| Field | Value |
|--------|--------|
| **Region** | `eu-central-1` |
| **Instance ID** | `i-0cd5389584d70a7fc` |
| **Public IP** | `3.122.252.186` (may change unless Elastic IP) |
| **IAM instance profile** | `EC2-SSM-Profile` |
| **On-host agent** | `amazon-ssm-agent` (snap), systemd **enabled** + **active** |

## Prerequisites (your laptop)

- AWS CLI v2 (or v1) configured with credentials that allow `ssm:StartSession` on this instance.
- IAM user/role policy should include at least:
  - `ssm:StartSession` (resource: instance ARN or `*`)
  - Often `ssm:TerminateSession`, `ssm:ResumeSession`
  - For port forwarding documents: same + trust on `ssm:StartSession` for document

## Connect (CLI)

```bash
export AWS_REGION=eu-central-1
aws ssm start-session --target i-0cd5389584d70a7fc --region eu-central-1
```

## Connect (AWS Console)

EC2 → Instances → select instance → **Connect** → **Session Manager** tab → Connect.

## Persistence across reboots

The SSM Agent is installed as a **snap** and the systemd unit is **enabled**:

- Unit: `snap.amazon-ssm-agent.amazon-ssm-agent.service`
- After any reboot it should start automatically.

Verify on the box:

```bash
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent --no-pager
sudo systemctl is-enabled snap.amazon-ssm-agent.amazon-ssm-agent
~/xrp_claude_bot/scripts/verify-ssm-agent.sh
```

If the agent is ever **inactive** after boot:

```bash
sudo snap start --enable amazon-ssm-agent
sudo systemctl start snap.amazon-ssm-agent.amazon-ssm-agent
```

## Port forwarding (optional)

Forward local port to instance SSH (useful if you want `ssh -p` through SSM):

```bash
aws ssm start-session \
  --target i-0cd5389584d70a7fc \
  --region eu-central-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["22"],"localPortNumber":["9222"]}'
```

Then: `ssh -i /path/to/key.pem -p 9222 ubuntu@127.0.0.1`

## Private subnet note

This instance has a **public IP** and reaches SSM endpoints over the internet (443). If you move workloads to **private subnets** without a NAT gateway, add **VPC interface endpoints** for `ssm`, `ssmmessages`, `ec2messages` (and optionally `ec2`).

## Rotating / IP change

Session Manager uses **Instance ID**, not IP. If Elastic IP is not attached, SSH config `HostName` may drift; SSM is unaffected.
