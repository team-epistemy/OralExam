"""Upload the docx + pdf fixtures through the live ALB and poll to ready."""
import json, time, urllib.request, urllib.error

B = "http://epistemy-m3-int-571630445.us-west-2.elb.amazonaws.com"
OH = {"x-org-name": "berkeley", "x-user-id": "operator", "x-role": "professor"}

def call(method, url, data=None, headers=None, raw=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data else None)
    r = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def ingest(path, fname, mime):
    raw = open(path, "rb").read()
    h = {"x-user-id": "operator", "x-role": "professor", "content-type": "application/json"}
    _, pre = call("POST", B + "/materials:presign", data={
        "org_name": "berkeley", "course_name": "data101",
        "file_name": fname, "mime_type": mime, "bytes": len(raw)}, headers=h)
    pre = json.loads(pre)
    s, _ = call("PUT", pre["upload_url"], raw=raw, headers={"content-type": mime})
    print(f"{fname}: presign ok, PUT {s}")
    s, _ = call("POST", B + "/versions/%s/register" % pre["material_version_id"], headers=OH)
    vurl = B + "/materials/%s/versions" % pre["material_id"]
    for _ in range(40):
        _, vs = call("GET", vurl, headers=OH)
        v = [x for x in json.loads(vs)
             if x["material_version_id"] == pre["material_version_id"]][0]
        if v["status"] in ("ready", "failed"):
            print(f"{fname}: {v['status']}  (source_type={v['source_type']})")
            return v["status"]
        time.sleep(2)
    print(f"{fname}: timeout")
    return "timeout"

ingest("/tmp/lecture.docx", "lecture.docx",
       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
ingest("/tmp/lecture.pdf", "lecture.pdf", "application/pdf")
