"""CloudFront distribution fronting the ALB so browsers reach it over HTTPS."""
from __future__ import annotations
import time


def ensure_distribution(cf, alb_dns: str, comment: str) -> dict:
    """Create a CloudFront distribution for the ALB origin if none exists."""
    existing = _find(cf, comment)
    if existing:
        return existing
    cfg = _config(alb_dns, comment)
    dist = cf.create_distribution(DistributionConfig=cfg)["Distribution"]
    return {"id": dist["Id"], "domain": dist["DomainName"]}


def _find(cf, comment: str):
    """Return an existing distribution matching the comment, else None."""
    items = cf.list_distributions().get("DistributionList", {}).get("Items", [])
    for d in items:
        if d.get("Comment") == comment:
            return {"id": d["Id"], "domain": d["DomainName"]}
    return None


def _config(alb_dns: str, comment: str) -> dict:
    """Build the distribution config: HTTP-only origin, redirect to HTTPS."""
    return {"CallerReference": f"epistemy-m3-{int(time.time())}",
            "Comment": comment, "Enabled": True,
            "Origins": {"Quantity": 1, "Items": [_origin(alb_dns)]},
            "DefaultCacheBehavior": _behavior()}


def _origin(alb_dns: str) -> dict:
    """ALB origin reached over plain HTTP (CloudFront terminates TLS)."""
    return {"Id": "alb", "DomainName": alb_dns,
            "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443,
                                   "OriginProtocolPolicy": "http-only"}}


def _behavior() -> dict:
    """Forward everything, no caching, all methods, redirect to HTTPS."""
    return {"TargetOriginId": "alb", "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": _methods(),
            "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
            "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3"}


def _methods() -> dict:
    """Allow all HTTP methods so presign/register/PUT pass through."""
    all_m = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    return {"Quantity": 7, "Items": all_m,
            "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}}


def wait_deployed(cf, dist_id: str) -> None:
    """Poll until the distribution finishes deploying to edge locations."""
    for _ in range(60):
        status = cf.get_distribution(Id=dist_id)["Distribution"]["Status"]
        if status == "Deployed":
            return
        time.sleep(30)
