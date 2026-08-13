# M3 networking — public access (permanent design)

## Why this shape

An Amazon internal security control (**Epoxy / epoxy-mitigations-prod /
ELBListenerDelete**) automatically deletes plaintext **HTTP:80 listeners on
internet-facing ALBs**. Our first attempt used an internet-facing ALB with an
HTTP listener, so the listener was repeatedly deleted (~every 30 min), breaking
the endpoint.

The permanent fix complies with the policy instead of fighting it: the ALB is
**internal** (Epoxy only targets internet-facing ALBs), and **CloudFront** is the
public HTTPS front door, reaching the internal ALB through a **VPC origin**.

## Topology

```
Browser ──HTTPS 443──> CloudFront (d3sz6c59gy5hwc.cloudfront.net)
                          │  VPC origin (managed ENIs, sg-0564741c90ddabfba)
                          ▼
                       Internal ALB (internal-epistemy-m3-int-...)  HTTP:80
                          ▼  target group epistemy-m3-int-tg :8080
                       ECS Fargate task (http container)
```

## Live resources (us-west-2, account 881432542692)

| Resource | Identifier |
|----------|-----------|
| CloudFront distribution | `E1HX5SP5L9GX5V` → `https://d3sz6c59gy5hwc.cloudfront.net` |
| CloudFront VPC origin | `vo_1EjdyvdYguIBo17Eqm0kC3` (http-only to ALB:80) |
| Internal ALB | `epistemy-m3-int` (scheme=internal) |
| Target group | `epistemy-m3-int-tg` (HTTP:8080, /health check) |
| ALB / task SG | `sg-07df906745ac17480` — inbound 80 from CloudFront ENI SG `sg-0564741c90ddabfba` |
| CloudFront ENI SG | `sg-0564741c90ddabfba` (AWS-managed for the VPC origin) |

## Security group rules that matter

- Internal ALB SG (`sg-07df906745ac17480`) allows **inbound TCP 80 from
  `sg-0564741c90ddabfba`** (the CloudFront VPC-origin ENIs). This is the only
  path in; the ALB has no public IP.
- Port 5432 self-referencing rule remains for Aurora.

## Removed (no longer used)

- Old internet-facing ALB `epistemy-m3` and target group `epistemy-m3-tg` — deleted.
- Debugging SG rules (0.0.0.0/0, corp NAT CIDRs, my-IP:8080) — removed.

## If the endpoint ever breaks again

1. Check the internal ALB listener exists (it should now persist, since Epoxy
   ignores internal ALBs):
   `aws elbv2 describe-listeners --load-balancer-arn <internal-alb-arn>`
2. Check the target is healthy in `epistemy-m3-int-tg`.
3. Check CloudFront VPC origin status is `Deployed` and the ALB SG still allows
   `sg-0564741c90ddabfba` on port 80.
