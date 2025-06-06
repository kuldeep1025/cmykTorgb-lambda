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


def is_rgb(image):
    """Check if the image is in Non-RGB mode."""
    logger.info(f"Checking if image is RGB: {image.mode}")
    return image.mode in("RGB")

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
    
def update_conversion_time(bucket, key, conversion_time):
    try:
        response = s3_client.get_object_tagging(Bucket=bucket, Key=key)
        tags = response.get('TagSet', [])
        tags = [tag for tag in tags if tag['Key'] != 'conversionTimeSec']
        tags.append({'Key': 'conversionTimeSec', 'Value': str(conversion_time)})
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={'TagSet': tags}
        )
        logger.info(f"Updated conversion time for {key} to {conversion_time} seconds")  
    except Exception as e:
        logger.error(f"Error updating conversion time for {key}: {str(e)}")
    
def get_image_tags(bucket, key):
    """Retrieve tags for the image from S3."""
    try:
        response = s3_client.get_object_tagging(Bucket=bucket, Key=key)
        tags = response.get('TagSet', [])
        return {tag['Key']: tag['Value'] for tag in tags}
    except Exception as e:
        logger.error(f"Error retrieving tags for {key}: {str(e)}")
        return {}


def lambda_handler(event, context):
    """Lambda function to convert CMYK images to RGB."""
    logger.info("Lambda function started")
    # Log the entire event body
    logger.info(f"Received event: {json.dumps(event, indent=2)}")
    # Define the bucket
    bucket = os.environ['TARGET_BUCKET_NAME']
    cdn_base_url = os.environ['CDN_BASE_URL']
    distribution_id= os.environ.get('CLOUDFRONT_DISTRIBUTION_ID')
    result = {"status": "success", "message": "", "processed_files": []}
    slack_token = getSecret('SLACK_CMYKTORGB_ALERT_API_TOKEN')
    start_time=time.time()
    retries=0
    buffer= None
    
    
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
            
            try:
                # Get the tags for the image
                tags = get_image_tags(bucket, key)
                logger.info(f"Tags for {key}: {tags}")
                if 'isRgbProcessed' in tags:
                    logger.info(f"File {key} has already been processed to RGB, skipping.")
                    result["processed_files"].append({"file": key, "status": "skipped", "reason": "Already processed to RGB"})
                    return
                
            except Exception as e:
                logger.error(f"Error retrieving tags for {key}: {str(e)}")
                tags = {}
                
            # Get file metadata to retrieve original file name
            try:
                metadata = s3_client.head_object(Bucket=bucket, Key=key)['Metadata']
                original_file_name = metadata.get('original_file_name', key.split('/')[-1])
                if len(original_file_name) > 8:
                    original_file_name = original_file_name[8:]
            except Exception as e:
                logger.error(f"Error fetching metadata for {key}: {str(e)}")
                original_file_name = key.split('/')[-1]
            
            # Download the file
            logger.info(f"Downloading {key} from bucket {bucket}")
            try:
                file_obj = s3_client.get_object(Bucket=bucket, Key=key)
                file_content = file_obj['Body'].read()
            except Exception as e:
                logger.error(f"Error downloading {key} from S3: {str(e)} \n Retry Count: {attempt}")
                send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', f"Error while image Downloading \n System Error : {str(e)}", slack_token, retries)
                result["processed_files"].append({"file": key, "status": "failed", "reason": str(e)})
                continue
            
            # Open image with Pillow
            image = Image.open(io.BytesIO(file_content))
            logger.info(f"Image mode: {image.mode}")
            # Check if non-rgb image
            if is_rgb(image):
                logger.info(f"File {key} is in RGB Format, no conversion needed")
                result["processed_files"].append({"file": key, "status": "skipped", "reason": "Not Non-RGB"})
                return 
                
            # retries limit exceeded 
            retries = 3
            for attempt in range(retries):
                try:
                    # Convert to RGB
                    logger.info(f"Converting {key} to RGB")
                    rgb_image = convert_to_rgb(image)

                    # Save converted image to buffer
                    buffer = io.BytesIO()
                    rgb_image.save(buffer, format=image.format or 'JPEG')
                    buffer.seek(0)
                    break  # Exit the retry loop on success
                except Exception as e:
                    logger.error(f"Error processing image {key}: {str(e)} \n Retry Count: {attempt}")
                    if attempt == retries-1:
                        send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', f"Error while image processing \n System Error : {str(e)}", slack_token, retries)
                    continue
                
            # Upload converted file
            logger.info(f"Uploading converted file to {key}")
            try:
                s3_client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=buffer,
                    ContentType=file_obj.get('ContentType', 'image/jpeg'),
                    Tagging="isRgbProcessed=true",
                )
                logger.info(f"Successfully converted and uploaded {key}")
                result["processed_files"].append({"file": key, "status": "converted", "reason": "Converted to RGB"})
            except Exception as e:
                logger.error(f"Error Uploading {key} to S3 : {str(e)}")
                send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', f"Error while Uploading image to S3 after conversion \n System Error : {str(e)}", slack_token, retries)
                continue
            
            try:
                conversion_time = int(time.time()-start_time)
                update_conversion_time(bucket, key, conversion_time)
            except Exception as e:
                logger.error(f"Error tagging Conversion Time for {key}: {str(e)}")
                send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', f"Error while tagging Conversion Time \n System Error: {str(e)}", slack_token, retries)
                continue
            
            try:
                # Invalidate CDN cache
                invalidate_CDN_cache(distribution_id, key)
                logger.info(f"CDN cache invalidated for {key}")
            except Exception as e:
                logger.error(f"Error invalidating CDN cache for {key}: {str(e)}")
                send_slack_notification(original_file_name, f'{cdn_base_url}/{key}', f"Error while invalidating CDN cache \n System Error : {str(e)}", slack_token, retries)
                continue
                
    except Exception as e:
        logger.error(f"Error processing event: {str(e)}")
        result["status"] = "error"
        cdn_url = f'{cdn_base_url}/{key}'
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