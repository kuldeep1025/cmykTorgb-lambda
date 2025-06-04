import json
import boto3
import logging
from PIL import Image
import io
import requests
import os
import time

# Initialize AWS S3 client
s3_client = boto3.client('s3')
secret_client = boto3.client('secretsmanager')
cloudfront_client = boto3.client('cloudfront')

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def getSecret(secret_name):
    """Retrieve the secret from AWS Secrets Manager."""
    try:
        response = secret_client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])['slack_api_token']
    except Exception as e:
        logger.ingo(f"Error retrieving secret {secret_name}: {str(e)}")
        raise 
    
def send_slack_notification(original_file_name,cdn_url,error_message,slack_token,retries):
    slack_url="https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {slack_token}"}
    payload={
        "channel":"#cmyktorgb-alerts",
        "text":(
             f"CMYK to RGB Conversion Failed\n"
            f"Original File: {original_file_name}\n"
            f"CDN URL: {cdn_url}\n"
            f"Retries: {retries}\n"
            f"Error: {error_message}"
        )
    }
    try:
        response = requests.post(slack_url,json=payload,headers=headers)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")


def is_cmyk(image):
    """Check if the image is in CMYK mode."""
    return image.mode == 'CMYK'

def convert_to_rgb(image):
    """Convert CMYK image to RGB."""
    return image.convert('RGB')

def invalidate_CDN_cache(distribution_id, key):
    """Invalidate the CDN cache for the given key."""
    try:
        response = cloudfront_client.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                'Paths': {
                    'Quantity': 1,
                    'Items': [f'/{key}']
                },
                'CallerReference': str(hash(key))  # Unique reference for the invalidation
            }
        )
        logger.info(f"CDN cache invalidation initiated for {key}: {response}")
    except Exception as e:
        logger.error(f"Error invalidating CDN cache: {str(e)}")
        raise
    
def get_retry_count(bucket,key):
    """Get the retry count from S3 object tags."""
    try:
        response = s3_client.get_object_tagging(Bucket=bucket, Key=key)
        logger.info(f"Tags for {key}: {response['TagSet']}")
        tags = {tag['Key']: tag['Value'] for tag in response['TagSet']}
        return int(tags.get('retryCount', 0))
    except Exception as e:
        logger.error(f"Error fetching retry count for {key}: {str(e)}")
        return 0

def update_retry_count(bucket,key,retries):
    """Update the retry count in S3 object tags."""
    try:
        response = s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                'TagSet': [
                    {'Key': 'retries', 'Value': str(retries)}
                ]
            }
        )
        logger.info(f"Updated retries tag to {retries} for {key} with response: {response}")
    except Exception as e:
        logger.error(f"Error updating retry count for {key}: {str(e)}")
        
def update_conversion_time(bucket, key, conversion_time):
    try:
        response = s3_client.get_object_tagging(Bucket=bucket, Key=key)
        tags = response.get('TagSet', [])
        tags=[tag for tag in tags if tag['Key'] != 'conversionTimeSec']
        tags.append({'Key': 'conversionTimeSec', 'Value': str(conversion_time)})
        s3_client.put_object_tagging(
            Bucket = bucket,
            key=key,
            Tagging={
                'Tagset':tags
            }
        )
        logger.info(f"Updated conversion time for {key} to {conversion_time} seconds")  
    except Exception as e:
        logger.error(f"Error updating conversion time for {key}: {str(e)}")


def lambda_handler(event, context):
    """Lambda function to convert CMYK images to RGB."""
    logger.info("Lambda function started")
    # Log the entire event body
    logger.info(f"Received event: {json.dumps(event, indent=2)}")
    # Define the bucket
    bucket = "ss-au-bank-preprod"
    cdn_base_url = os.environ['CDN_BASE_URL']
    distribution_id= os.environ.get('CLOUDFRONT_DISTRIBUTION_ID')
    result = {"status": "success", "message": "", "processed_files": []}
    slack_token = getSecret('SLACK_CMYKTORGB_ALERT_API_TOKEN')
    start_time=time.time()
    retries=0
    try:
        # Process each record in the event
        for record in event['Records']:
            key = record['s3']['object']['key']
            logger.info(f"Processing file: {key}")

            # Skip files not in smartsell/pages_1/
            if not key.startswith('smartsell/pages_1/'):
                logger.info(f"Skipping file {key}: not in smartsell/pages_1/")
                result["processed_files"].append({"file": key, "status": "skipped", "reason": "Not in smartsell/pages_1/"})
                continue
            
              # Get file metadata to retrieve original file name
            try:
                metadata = s3_client.head_object(Bucket=bucket, Key=key)['Metadata']
                original_file_name = metadata.get('original_file_name', key.split('/')[-1])
            except Exception as e:
                logger.error(f"Error fetching metadata for {key}: {str(e)}")
                original_file_name = key.split('/')[-1]
                
            # retries limit exceeded 
            retries = get_retry_count(bucket, key)
            if(retries >= 3):
                logger.info(f"Skipping {key} as it has reached the maximum retry limit of 3")
                send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', "Max retries reached", slack_token, retries)
                continue

            # Download the file
            logger.info(f"Downloading {key} from bucket {bucket}")
            try:
                file_obj = s3_client.get_object(Bucket=bucket, Key=key)
                file_content = file_obj['Body'].read()
            except Exception as e:
                logger.error(f"Error downloading {key} from S3: {str(e)}")
                retries += 1
                update_retry_count(bucket, key, retries)
                send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', str(e), slack_token, retries)
                result["processed_files"].append({"file": key, "status": "failed", "reason": str(e)})
                continue
            
            # Open image with Pillow
            try:
                image = Image.open(io.BytesIO(file_content))
                logger.info(f"Image mode: {image.mode}")

                # Check if CMYK
                if not is_cmyk(image):
                    logger.info(f"File {key} is not CMYK, no conversion needed")
                    result["processed_files"].append({"file": key, "status": "skipped", "reason": "Not CMYK"})
                    continue

                # Convert to RGB
                logger.info(f"Converting {key} to RGB")
                rgb_image = convert_to_rgb(image)

                # Save converted image to buffer
                buffer = io.BytesIO()
                rgb_image.save(buffer, format=image.format or 'JPEG')
                buffer.seek(0)

                # Upload converted file
                logger.info(f"Uploading converted file to {key}")
                s3_client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=buffer,
                    ContentType=file_obj.get('ContentType', 'image/jpeg'),
                )
                
                # update conversion time
                conversion_time = start_time - time.time()
                update_conversion_time(bucket, key, conversion_time)
                
                logger.info(f"Successfully converted and uploaded {key}")
                result["processed_files"].append({"file": key, "status": "converted", "reason": "Converted to RGB"})
                
                # Invalidate CDN cache
                invalidate_CDN_cache(distribution_id, key)

            except Exception as e:
                logger.error(f"Error processing {key}: {str(e)}")
                result["processed_files"].append({"file": key, "status": "failed", "reason": str(e)})

    except Exception as e:
        logger.error(f"Error processing event: {str(e)}")
        result["status"] = "error"
        cdn_url = f'{cdn_base_url}/{key}'
        retries += 1
        send_slack_notification(original_file_name, cdn_url, str(e), slack_token,retries)

    # Return result
    logger.info(f"Returning result: {json.dumps(result, indent=2)}")
    return {
        'statusCode': 200,
        'body': json.dumps(result)
    }
    
# if __name__ == "__main__":
#     from pprint import pprint
    
#     sample_event ={
#   "Records": [
#     {
#       "eventVersion": "2.1",
#       "eventSource": "aws:s3",
#       "awsRegion": "ap-south-1",
#       "eventTime": "2025-06-03T04:45:09.042Z",
#       "eventName": "ObjectCreated:Put",
#       "userIdentity": {
#         "principalId": "AWS:AROARTSECBSR754YUR4GJ:kuldeep.shakya@sharpsell.ai"
#       },
#       "requestParameters": {
#         "sourceIPAddress": "43.224.159.17"
#       },
#       "responseElements": {
#         "x-amz-request-id": "236FD4DC5XG8D600",
#         "x-amz-id-2": "/LQ9TwaY3I1L6O5HXxRGbxglWnKLbuS/GjuFlWiESXkj/3VhCNr++hFdeKBRD0a4QUV08Uo8uxjLHO1yCMzSWGdkGMRMLvFLvU6JRpoY6AM="
#       },
#       "s3": {
#         "s3SchemaVersion": "1.0",
#         "configurationId": "cmyk-upload-trigger",
#         "bucket": {
#           "name": "ss-au-bank-preprod",
#           "ownerIdentity": {
#             "principalId": "A147FBF2EKRJEQ"
#           },
#           "arn": "arn:aws:s3:::ss-au-bank-preprod"
#         },
#         "object": {
#           "key": "smartsell/pages_1/1727367408_dvc_poster_cmyk.jpg",
#           "size": 3459760,
#           "eTag": "3231172aceb791c7116f87cff763ac4f",
#           "sequencer": "00683E7DD4E0520BCA"
#         }
#       }
#     }
#   ]
#  }   
   
#     # send_slack_notification()
#     pprint(lambda_handler(sample_event, None))