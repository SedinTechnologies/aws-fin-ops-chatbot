#!/bin/bash

awslocal s3api \
    create-bucket --bucket aws-fin-ops-bot-data \
    --create-bucket-configuration LocationConstraint=eu-central-1 \
    --region eu-central-1
echo '{"CORSRules":[{"AllowedHeaders":["*"],"AllowedMethods":["GET","POST","PUT"],"AllowedOrigins":["*"],"ExposeHeaders":["ETag"]}]}' > cors.json
awslocal s3api put-bucket-cors --bucket aws-fin-ops-bot-data --cors-configuration file://cors.json
