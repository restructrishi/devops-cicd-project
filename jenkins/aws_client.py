#!/usr/bin/env python3
"""
AWS Client Initialization for Jenkins Infrastructure
"""

import boto3
import sys
from botocore.exceptions import NoCredentialsError, ClientError


def print_log(message, level="INFO"):
    """Print log message to stdout"""
    timestamp = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def initialize_aws_clients(region):
    """
    Initialize AWS clients for Jenkins infrastructure
    
    Args:
        region: AWS region
        
    Returns:
        dict: Dictionary of initialized boto3 clients
    """
    try:
        clients = {
            'ec2': boto3.client('ec2', region_name=region),
            'iam': boto3.client('iam', region_name=region),
            's3': boto3.client('s3', region_name=region),
            'codebuild': boto3.client('codebuild', region_name=region),
            'codedeploy': boto3.client('codedeploy', region_name=region),
            'codestar': boto3.client('codestar-connections', region_name=region),
            'sts': boto3.client('sts')
        }
        
        # Verify credentials
        identity = clients['sts'].get_caller_identity()
        print_log(f"AWS clients initialized successfully")
        print_log(f"  Running as: {identity['Arn']}")
        print_log(f"  Account ID: {identity['Account']}")
        print_log(f"  Region: {region}")
        
        return clients
        
    except NoCredentialsError:
        print_log("AWS credentials not configured!", "ERROR")
        sys.exit(1)
    except ClientError as e:
        print_log(f"Failed to initialize AWS clients: {e}", "ERROR")
        sys.exit(1)
    except Exception as e:
        print_log(f"Unexpected error initializing AWS clients: {e}", "ERROR")
        sys.exit(1)


def get_account_id(sts_client):
    """
    Get AWS account ID
    
    Args:
        sts_client: boto3 STS client
        
    Returns:
        str: AWS account ID
    """
    try:
        return sts_client.get_caller_identity()['Account']
    except Exception as e:
        print_log(f"Failed to get account ID: {e}", "ERROR")
        sys.exit(1)