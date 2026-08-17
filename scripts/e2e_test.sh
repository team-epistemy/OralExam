#!/usr/bin/env bash
# End-to-end M3 test via the ALB. Run from AWS CloudShell or any host that can
# reach the ALB. Usage: BASE=http://<alb-dns> ./e2e_test.sh
set -euo pipefail

BASE="${BASE:?set BASE to the ALB URL, e.g. http://epistemy-m3-xxxx.us-west-2.elb.amazonaws.com}"
ORG="11111111-1111-1111-1111-111111111111"
USER="22222222-2222-2222-2222-222222222222"
COURSE="33333333-3333-3333-3333-333333333333"

echo "== 1. health =="
curl -fsS "$BASE/health"; echo

echo "== 2. create a test markdown file =="
printf '# My Lecture\n\nIntro text.\n\n## Topic A\n\nBody about topic A.\n' > /tmp/lecture.md
BYTES=$(wc -c < /tmp/lecture.md)
echo "bytes=$BYTES"

echo "== 3. presign =="
PRESIGN=$(curl -fsS -X POST "$BASE/courses/$COURSE/materials:presign" \
  -H "content-type: application/json" \
  -H "x-org-id: $ORG" -H "x-user-id: $USER" -H "x-role: professor" \
  -d "{\"file_name\":\"lecture.md\",\"mime_type\":\"text/markdown\",\"bytes\":$BYTES}")
echo "$PRESIGN"
URL=$(echo "$PRESIGN" | python3 -c "import sys,json;print(json.load(sys.stdin)['upload_url'])")
VID=$(echo "$PRESIGN" | python3 -c "import sys,json;print(json.load(sys.stdin)['material_version_id'])")

echo "== 4. upload bytes straight to S3 via the presigned URL =="
curl -fsS -X PUT "$URL" \
  -H "content-type: text/markdown" \
  -H "x-amz-server-side-encryption: aws:kms" \
  --data-binary @/tmp/lecture.md
echo "uploaded"

echo "== 5. register (enqueues ingest job) =="
curl -fsS -X POST "$BASE/versions/$VID:register" \
  -H "x-org-id: $ORG" -H "x-user-id: $USER" -H "x-role: professor"; echo

echo "== 6. done. Inspect Aurora via the status query below. =="
echo "version_id=$VID"
