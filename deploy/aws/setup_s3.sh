#!/usr/bin/env bash
# Create the production bucket on AWS with the security controls from the report
# (Module 4: IAM + KMS). Template: review before running; needs the AWS CLI and
# permissions to create buckets, KMS keys and IAM policies.
#   BUCKET=my-imgframe-bucket REGION=ap-south-1 bash deploy/aws/setup_s3.sh
set -euo pipefail
: "${BUCKET:?set BUCKET}"; REGION="${REGION:-ap-south-1}"
cd "$(dirname "$0")"

# 1. Customer-managed KMS key for SSE-KMS
KEY_ARN=$(aws kms create-key --region "$REGION" --description "imgframe tiles" \
          --query KeyMetadata.Arn --output text)
aws kms enable-key-rotation --region "$REGION" --key-id "$KEY_ARN"

# 2. Bucket, private, encrypted by default with the key
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    "{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"$KEY_ARN\"},\"BucketKeyEnabled\":true}]}"

# 3. Deny unencrypted / wrong-key / non-TLS requests
sed -e "s|BUCKET|$BUCKET|g" -e "s|KMS_KEY_ARN|$KEY_ARN|g" bucket-policy.json > /tmp/imgframe-bucket-policy.json
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/imgframe-bucket-policy.json

# 4. Lifecycle: job objects expire after a day (replaces RESULT_TTL_SECONDS)
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration file://lifecycle.json

# 5. Least-privilege policies for the two service roles (attach to IRSA roles)
for svc in api worker; do
    sed -e "s|BUCKET|$BUCKET|g" -e "s|KMS_KEY_ARN|$KEY_ARN|g" "iam-$svc-policy.json" > "/tmp/imgframe-$svc.json"
    aws iam create-policy --policy-name "imgframe-$svc-s3" --policy-document "file:///tmp/imgframe-$svc.json"
done
echo "bucket $BUCKET ready; KMS key $KEY_ARN"
echo "set in k8s ConfigMap: S3_BUCKET=$BUCKET S3_SSE=aws:kms S3_KMS_KEY_ID=$KEY_ARN (and remove S3_ENDPOINT)"
