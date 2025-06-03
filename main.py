import json
import boto3
import logging
from PIL import Image
import io
from pprint import pprint

# Initialize AWS S3 client
s3_client = boto3.client('s3')

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def is_cmyk(image):
    """Check if the image is in CMYK mode."""
    return image.mode == 'CMYK'

def convert_to_rgb(image):
    """Convert CMYK image to RGB."""
    return image.convert('RGB')


def lambda_handler(event, context):
    """Lambda function to convert CMYK images to RGB."""
    logger.info("Lambda function started")
    # Log the entire event body
    logger.info(f"Received event: {json.dumps(event, indent=2)}")
    # Define the bucket
    bucket = "ss-au-bank-preprod"
    result = {"status": "success", "message": "", "processed_files": []}

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

            # Download the file
            logger.info(f"Downloading {key} from bucket {bucket}")
            file_obj = s3_client.get_object(Bucket=bucket, Key=key)
            file_content = file_obj['Body'].read()

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
                    Tagging="isCmykProcessed=true"
                )
                logger.info(f"Successfully converted and uploaded {key}")
                result["processed_files"].append({"file": key, "status": "converted", "reason": "Converted to RGB"})

            except Exception as e:
                logger.error(f"Error processing {key}: {str(e)}")
                result["processed_files"].append({"file": key, "status": "failed", "reason": str(e)})

    except Exception as e:
        logger.error(f"Error processing event: {str(e)}")
        result["status"] = "error"
        result["message"] = str(e)

    # Return result
    logger.info(f"Returning result: {json.dumps(result, indent=2)}")
    return {
        'statusCode': 200,
        'body': json.dumps(result)
    }
    
# if __name__ == "__main__":
#    sample_event ={
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
#    pprint(lambda_handler(event, None))