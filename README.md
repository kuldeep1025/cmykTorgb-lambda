**📌 Purpose**

This Lambda function is triggered when an image is uploaded to a specific path in an S3 bucket (`ss-au-bank-preprod/smartsell/pages_1/`). It checks if the image is in **CMYK** color space and, if so, converts it to **RGB**, uploads it back to S3 (overwriting the original), and **invalidates the CloudFront CDN cache** for the updated file.

### ✅ Core Features

- ✅ Detect Non-RGB images using PIL (Pillow)
- ✅ Convert Non-RGB images to RGB
- ✅ Re-upload image to S3 at the same key a
- ✅ Invalidate CloudFront cache
- ✅ Record `conversionTimeSec` and `isRGBProcessed` as an S3 tag
- ✅ Send Slack alerts on failure with relevant metadata

⚙️ Environment Variables

| Variable | Description |
| --- | --- |
| `CDN_BASE_URL` | Base URL of the CDN (used for Slack notifications) |
| `CLOUDFRONT_DISTRIBUTION_ID` | CloudFront distribution ID for cache invalidation |
| `TARGET_BUCKET_NAME` | S3 bucket name for triggering lambda |

🔐 Secrets

| Secret Name | Description |
| --- | --- |
| `SLACK_CMYKTORGB_ALERT_API_TOKEN`  = {`slack_api_token`:’token_value’} |
 | Slack Bot OAuth token to send alerts to Slack channel `#cmyktorgb-alerts` |

**🚨 Failure Scenarios & Slack Alerts**

| **Scenario** | **Slack Alert Triggered** | **Slack Message Preview** |
| --- | --- | --- |
| File not in `smartsell/pages_1/` | ❌ No | *No alert — file ignored silently* |
| File is already tagged `isCmykProcessed: true` | ❌ No | *No alert — file considered already processed* |
| File retry count >= 3 while image conversion | ✅ Yes | `Max retries reached for file: {filename}` |
| S3 download failure | ✅ Yes | `Error while downloading file from S3: {filename}` |
| Image is invalid or corrupted (Pillow error) | ✅ Yes | `Library related issues occurred while processing file: {filename}` |
| Image is not Non-RGB (e.g., already RGB) | ❌ No | *No alert — image skipped as it's not in CMYK mode* |
| Conversion succeeded | ❌ No | *No alert — success* |
| Upload back to S3 fails | ✅ Yes | `Failed to upload RGB image to S3: {filename}` |
| Tagging fails | ✅ Yes | `Failed to tag image on S3: {filename}` |
| CloudFront invalidation fails | ✅ Yes | `CloudFront invalidation failed for: {cdn_url}` |
| Any unexpected runtime exception | ✅ Yes | `{exception error message}` (dynamically included) |

**Slack message example:**

```
CMYK to RGB Conversion Failed
Original File: abc123.pdf
CDN URL: https://cdn.example.com/smartsell/pages_1/abc123.pdf
Retries: 3
Error: Max retries reached
System Error: Reason for the error

```

### ✅ AWS Setup

- ✅ Assumes your **S3 bucket** and **CloudFront distribution** are already set up.
    - **A. Create a Lambda Function**
        - Go to AWS Lambda → Create function → Author from scratch.
        - Assign a name, e.g., `cmyk-to-rgb-converter`.
        - Choose **Python 3.9+** as the runtime.
        - Upload the zipped deployment package or deploy via SAM/Serverless Framework.
        - Add Pillow and Requests dependencies as layers
        
    - **B. Create & Attach an IAM Role to Lambda**
        - You will need to create an **IAM Role** and attach it to the Lambda function with these permissions:
    
    **S3 Access:**
    
    - Permission to:
        - Get objects
        - Put objects
        - Get and put object tags
    - Only for files under: `your-bucket-name/smartsell/pages_1/`
    
    **CloudFront Access:**
    
    - Permission to create cache invalidations
    
    **Secrets Manager Access:**
    
    - Permission to read one secret:
        - Name: `SLACK_CMYKTORGB_ALERT_API_TOKEN`
    
    ### C. Set Environment Variables
    
    In Lambda → Configuration → Environment variables, add:
    
    | Key | Value |
    | --- | --- |
    | `TARGET_BUCKET_NAME` | your S3 bucket name |
    | `CDN_BASE_URL` | your CloudFront URL (e.g., `https://cdn.example.com`) |
    | `CLOUDFRONT_DISTRIBUTION_ID` | Your CloudFront distribution ID |

### D. Store Slack Token in Secrets Manager

1. Go to **Secrets Manager > Store a new secret**.
2. Choose **Other type of secret**.
3. Set the key-value pair:
    
    ```
    {
      "slack_api_token": "xoxb-XXXXXXXXXX"
    }
    ```
    
    Name it exactly: `SLACK_CMYKTORGB_ALERT_API_TOKEN`
    

### 🔁 S3 Event Trigger Setup

1. Go to **S3 > Your Bucket > Properties > Event notifications**
2. Add a new event notification:
    - **Event type:** `PUT` (ObjectCreated)
    - **Prefix filter:** `smartsell/pages_1/`
    - **Destination:** Lambda → Select your function

### 🧪 Debugging & Observability

- View logs in **CloudWatch Logs** → Linked from the Lambda function page.
- Failures trigger a **Slack message** to `#cmyktorgb-alerts` with:
    - File name
    - CDN URL
    - Retry count
    - Error message


