#!/usr/bin/env bash
# Provision one robot as an IoT Thing: create the thing, generate a keypair and
# X.509 cert, attach the Beacon robot policy, and save the cert files locally.
#
# Usage: ./provision_thing.sh robot-001 [region] [policy-name]
set -euo pipefail

DEVICE_ID="${1:?usage: provision_thing.sh <device-id> [region] [policy]}"
REGION="${2:-us-west-2}"
POLICY="${3:-beacon-robot-policy}"
CERT_DIR="./certs"
mkdir -p "$CERT_DIR"

echo "creating thing $DEVICE_ID"
aws iot create-thing --thing-name "$DEVICE_ID" --region "$REGION" >/dev/null

echo "creating cert + keys"
CERT_ARN=$(aws iot create-keys-and-certificate \
  --set-as-active \
  --region "$REGION" \
  --certificate-pem-outfile "$CERT_DIR/$DEVICE_ID.cert.pem" \
  --public-key-outfile "$CERT_DIR/$DEVICE_ID.public.key" \
  --private-key-outfile "$CERT_DIR/$DEVICE_ID.private.key" \
  --query certificateArn --output text)

echo "attaching policy + thing"
aws iot attach-policy --policy-name "$POLICY" --target "$CERT_ARN" --region "$REGION"
aws iot attach-thing-principal --thing-name "$DEVICE_ID" --principal "$CERT_ARN" --region "$REGION"

if [ ! -f "$CERT_DIR/AmazonRootCA1.pem" ]; then
  echo "downloading Amazon root CA"
  curl -s https://www.amazontrust.com/repository/AmazonRootCA1.pem -o "$CERT_DIR/AmazonRootCA1.pem"
fi

echo "done. certs in $CERT_DIR/"
