#!/usr/bin/env python3

"""
IAM Automation Script
Functional approach for creating IAM policies, roles, and instance profiles.
Called by main.sh with CLI arguments.
"""

import boto3
import json
import sys
import argparse
from pathlib import Path
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError

# Add parent directory to path to import readconfig
sys.path.append(str(Path(__file__).parent.parent))
from readconfig import get_config_value, get_config_values

# Import IAM validators for state checking - Pure Functional Approach
from validators import (
    validate_aws_credentials,
    validate_all_policies, 
    validate_all_roles,
    validate_all_instance_profiles,
    validate_s3_bucket_configuration
)

def print_log(message: str, level: str = "INFO", flush: bool = False) -> None:
    """
    Print log message to stdout (will be captured by main.sh piping).
    
    Args:
        message: Log message
        level: Log level (INFO, ERROR, WARNING)
        flush: Force flush output buffer (useful for real-time progress)
    """
    timestamp = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] [{level}] {message}", flush=flush)


def validate_aws_credentials(region: str) -> bool:
    """
    Validate AWS credentials and return account info.
    
    Args:
        region: AWS region
        
    Returns:
        True if credentials are valid, False otherwise
    """
    try:
        sts = boto3.client('sts', region_name=region)
        identity = sts.get_caller_identity()
        print_log(f" AWS credentials valid - Running as: {identity['Arn']}")
        return True
    except NoCredentialsError:
        print_log(" AWS credentials not configured!", "ERROR")
        return False
    except Exception as e:
        print_log(f" Error validating AWS credentials: {str(e)}", "ERROR")
        return False

def get_account_id(region: str) -> Optional[str]:
    """
    Get AWS account ID.
    
    Args:
        region: AWS region
        
    Returns:
        Account ID or None if error
    """
    try:
        sts = boto3.client('sts', region_name=region)
        return sts.get_caller_identity()['Account']
    except Exception as e:
        print_log(f" Error getting account ID: {str(e)}", "ERROR")
        return None

def get_policy_definitions(account_id: str, s3_bucket: str, connection_arn: str, region: str) -> Dict[str, Dict[str, Any]]:
    """
    Get policy definitions for customer managed policies.
    
    Args:
        account_id: AWS account ID
        s3_bucket: S3 bucket name
        connection_arn: CodeConnections ARN
        region: AWS region
        
    Returns:
        Dictionary of policy definitions
    """
    return {
        'TomcatS3AccessPolicy': {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{s3_bucket}",
                        f"arn:aws:s3:::{s3_bucket}/*"
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents"
                    ],
                    "Resource": "arn:aws:logs:*:*:*"
                }
            ]
        },
        'CodeDeployS3AccessPolicy': {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:GetObjectVersion",
                        "s3:ListBucket"
                    ],
                    "Resource": [
                        f"arn:aws:s3:::{s3_bucket}",
                        f"arn:aws:s3:::{s3_bucket}/*"
                    ]
                }
            ]
        },
        'CodeBuildCodeConnectionsSourceCredentialsPolicy-helloworld-us-east-1': {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "codeconnections:UseConnection",
                        "codeconnections:GetConnection",
                        "codeconnections:ListConnections"
                    ],
                    "Resource": f"arn:aws:codeconnections:{region}:{account_id}:connection/*"
                }
            ]
        }
    }

def get_role_definitions(account_id: str, connection_arn: str, tomcat_role: str, jenkins_role: str, 
                        codedeploy_instance_role: str, codedeploy_service_role: str, 
                        codebuild_service_role: str, tg1_role: str, s3_bucket: str) -> Dict[str, Dict[str, Any]]:
    """
    Get role definitions with their configurations.
    
    Args:
        account_id: AWS account ID
        connection_arn: CodeConnections ARN
        tomcat_role: Tomcat role name
        jenkins_role: Jenkins role name
        codedeploy_instance_role: CodeDeploy instance role name
        codedeploy_service_role: CodeDeploy service role name
        codebuild_service_role: CodeBuild service role name
        tg1_role: TG1 role name
        s3_bucket: S3 bucket name
        
    Returns:
        Dictionary of role definitions
    """
    return {
        tomcat_role: {
            'assume_role_policy': {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            },
            'managed_policies': [
                'arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess',
                'arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy',
                f'arn:aws:iam::{account_id}:policy/TomcatS3AccessPolicy'
            ],
            'inline_policies': {}
        },
        jenkins_role: {
            'assume_role_policy': {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            },
            'managed_policies': [
                'arn:aws:iam::aws:policy/AmazonS3FullAccess',
                'arn:aws:iam::aws:policy/AWSCodeBuildDeveloperAccess',
                'arn:aws:iam::aws:policy/AWSCodeDeployFullAccess'
            ],
            'inline_policies': {
                'JenkinsAutoScalingPolicy': {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "autoscaling:DescribeAutoScalingGroups",
                                "autoscaling:DescribeAutoScalingInstances",
                                "autoscaling:StartInstanceRefresh",
                                "autoscaling:DescribeInstanceRefreshes",
                                "autoscaling:CancelInstanceRefresh"
                            ],
                            "Resource": "*"
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ec2:DescribeInstances",
                                "ec2:DescribeInstanceStatus"
                            ],
                            "Resource": "*"
                        }
                    ]
                }
            }
        },
        codedeploy_instance_role: {
            'assume_role_policy': {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            },
            'managed_policies': [
                'arn:aws:iam::aws:policy/service-role/AmazonEC2RoleforAWSCodeDeploy',
                'arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess',
                'arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy',
                f'arn:aws:iam::{account_id}:policy/CodeDeployS3AccessPolicy'
            ],
            'inline_policies': {}
        },
        codedeploy_service_role: {
            'assume_role_policy': {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codedeploy.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            },
            'managed_policies': [
                'arn:aws:iam::aws:policy/service-role/AWSCodeDeployRole'
            ],
            'inline_policies': {}
        },
        codebuild_service_role: {
            'assume_role_policy': {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "codebuild.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            },
            'managed_policies': [
                'arn:aws:iam::aws:policy/AmazonS3FullAccess',
                'arn:aws:iam::aws:policy/CloudWatchLogsFullAccess'
            ],
            'inline_policies': {
                'CodeBuildGitHubConnectionPolicy': {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "CodeStarConnectionsAccess",
                            "Effect": "Allow",
                            "Action": [
                                "codestar-connections:UseConnection",
                                "codestar-connections:GetConnection",
                                "codestar-connections:ListConnections"
                            ],
                            "Resource": connection_arn
                        },
                        {
                            "Sid": "CodeConnectionsAccess",
                            "Effect": "Allow",
                            "Action": [
                                "codeconnections:UseConnection",
                                "codeconnections:GetConnection",
                                "codeconnections:ListConnections",
                                "codeconnections:GetConnectionToken"
                            ],
                            "Resource": connection_arn
                        },
                        {
                            "Sid": "GitHubAccess",
                            "Effect": "Allow",
                            "Action": ["codestar-connections:PassConnection"],
                            "Resource": connection_arn,
                            "Condition": {
                                "StringEquals": {
                                    "codestar-connections:PassedToService": "codebuild.amazonaws.com"
                                }
                            }
                        }
                    ]
                }
            }
        },
        tg1_role: {
            'assume_role_policy': {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            },
            'managed_policies': [
                'arn:aws:iam::aws:policy/AmazonKinesisFirehoseFullAccess',
                'arn:aws:iam::aws:policy/AmazonSNSFullAccess'
            ],
            'inline_policies': {}
        }
    }

def policy_exists(iam_client, policy_name: str, account_id: str) -> bool:
    """
    Check if a policy exists (idempotent check).
    
    Args:
        iam_client: boto3 IAM client
        policy_name: Name of the policy
        account_id: AWS account ID
        
    Returns:
        True if policy exists, False otherwise
    """
    try:
        policy_arn = f'arn:aws:iam::{account_id}:policy/{policy_name}'
        iam_client.get_policy(PolicyArn=policy_arn)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return False
        raise

def create_or_update_policy(iam_client, policy_name: str, policy_document: Dict[str, Any], account_id: str) -> bool:
    """
    Create or update a policy (idempotent).
    
    Args:
        iam_client: boto3 IAM client
        policy_name: Name of the policy
        policy_document: Policy document
        account_id: AWS account ID
        
    Returns:
        True if successful, False otherwise
    """
    try:
        policy_arn = f'arn:aws:iam::{account_id}:policy/{policy_name}'
        
        if policy_exists(iam_client, policy_name, account_id):
            # Policy exists, check if it needs updating
            existing_policy = iam_client.get_policy(PolicyArn=policy_arn)
            print_log(f" Policy {policy_name} already exists")
            
            # Verify policy document matches
            policy_version = iam_client.get_policy_version(
                PolicyArn=policy_arn,
                VersionId=existing_policy['Policy']['DefaultVersionId']
            )
            
            existing_doc = policy_version['PolicyVersion']['Document']
            if existing_doc != policy_document:
                print_log(f"  Updating policy {policy_name}...")
                iam_client.create_policy_version(
                    PolicyArn=policy_arn,
                    PolicyDocument=json.dumps(policy_document),
                    SetAsDefault=True
                )
                print_log(f"   Updated policy {policy_name}")
            else:
                print_log(f"   Policy {policy_name} is up to date")
        else:
            # Create new policy
            iam_client.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps(policy_document)
            )
            print_log(f" Created policy: {policy_name}")
        
        return True
        
    except ClientError as e:
        print_log(f" Failed to create/update policy {policy_name}: {e}", "ERROR")
        return False

def role_exists(iam_client, role_name: str) -> bool:
    """
    Check if a role exists (idempotent check).
    
    Args:
        iam_client: boto3 IAM client
        role_name: Name of the role
        
    Returns:
        True if role exists, False otherwise
    """
    try:
        iam_client.get_role(RoleName=role_name)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return False
        raise

def create_or_update_role(iam_client, role_name: str, role_config: Dict[str, Any], account_id: str) -> bool:
    """
    Create or update an IAM role (idempotent).
    
    Args:
        iam_client: boto3 IAM client
        role_name: Name of the IAM role
        role_config: Role configuration dictionary
        account_id: AWS account ID
        
    Returns:
        True if successful, False otherwise
    """
    role_exists_flag = False

    try:
        # Check if role exists
        if role_exists(iam_client, role_name):
            existing_role = iam_client.get_role(RoleName=role_name)
            role_exists_flag = True
            print_log(f" Role {role_name} already exists")

            # Verify assume role policy
            existing_assume_policy = existing_role['Role']['AssumeRolePolicyDocument']
            if existing_assume_policy != role_config['assume_role_policy']:
                print_log(f"  Updating assume role policy for {role_name}...")
                iam_client.update_assume_role_policy(
                    RoleName=role_name,
                    PolicyDocument=json.dumps(role_config['assume_role_policy'])
                )
                print_log(f"   Updated assume role policy")
        else:
            # Create new role
            iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(role_config['assume_role_policy']),
                Description=f"Role for {role_name}"
            )
            role_exists_flag = True
            print_log(f" Created role: {role_name}")

        if not role_exists_flag:
            return False

        # Manage attached policies
        manage_role_policies(iam_client, role_name, role_config)
        return True
        
    except ClientError as e:
        print_log(f" Failed to create/update role {role_name}: {e}", "ERROR")
        return False

def manage_role_policies(iam_client, role_name: str, role_config: Dict[str, Any]) -> None:
    """
    Manage role policies (managed and inline).
    
    Args:
        iam_client: boto3 IAM client
        role_name: Name of the IAM role
        role_config: Role configuration dictionary
    """
    try:
        # Get currently attached managed policies
        current_policies = iam_client.list_attached_role_policies(RoleName=role_name)
        current_policy_arns = {p['PolicyArn'] for p in current_policies['AttachedPolicies']}
        required_policy_arns = set(role_config['managed_policies'])
        
        # Attach missing policies
        policies_to_attach = required_policy_arns - current_policy_arns
        for policy_arn in policies_to_attach:
            try:
                iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
                print_log(f"   Attached policy {policy_arn.split('/')[-1]} to {role_name}")
            except ClientError as e:
                print_log(f"   Failed to attach {policy_arn}: {e}", "ERROR")
        
        # Detach extra policies (but NOT AWS managed policies we didn't add)
        policies_to_detach = current_policy_arns - required_policy_arns
        for policy_arn in policies_to_detach:
            # Only detach custom policies (account-specific), not AWS managed policies
            # This prevents accidentally removing manually added AWS policies
            if policy_arn.startswith('arn:aws:iam::aws:policy'):
                print_log(f"  ⚠ Skipping detachment of AWS managed policy: {policy_arn.split('/')[-1]} (not in config)", "WARNING")
                continue
            
            try:
                iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
                print_log(f"   Detached extra custom policy {policy_arn.split('/')[-1]}")
            except ClientError as e:
                print_log(f"  ⚠ Could not detach {policy_arn}: {e}", "WARNING")
        
        if not policies_to_attach and not policies_to_detach:
            print_log(f"   All managed policies correctly configured for {role_name}")
        elif policies_to_attach:
            print_log(f"   Attached {len(policies_to_attach)} missing policy(ies) to {role_name}")
                
    except ClientError as e:
        print_log(f"   Error managing policies for {role_name}: {e}", "ERROR")

    # Manage inline policies
    manage_inline_policies(iam_client, role_name, role_config)

def manage_inline_policies(iam_client, role_name: str, role_config: Dict[str, Any]) -> None:
    """
    Manage inline policies for a role.
    
    Args:
        iam_client: boto3 IAM client
        role_name: Name of the IAM role
        role_config: Role configuration dictionary
    """
    try:
        current_inline = iam_client.list_role_policies(RoleName=role_name)
        current_inline_names = set(current_inline['PolicyNames'])
        required_inline_names = set(role_config['inline_policies'].keys())
        
        # Add or update inline policies
        for policy_name, policy_document in role_config['inline_policies'].items():
            needs_update = False
            if policy_name in current_inline_names:
                existing_inline = iam_client.get_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name
                )
                if existing_inline['PolicyDocument'] != policy_document:
                    needs_update = True
            else:
                needs_update = True
            
            if needs_update:
                iam_client.put_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_document)
                )
                action = "Updated" if policy_name in current_inline_names else "Added"
                print_log(f"   {action} inline policy '{policy_name}' for {role_name}")
        
        # Remove extra inline policies
        policies_to_remove = current_inline_names - required_inline_names
        for policy_name in policies_to_remove:
            try:
                iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
                print_log(f"   Removed extra inline policy '{policy_name}' from {role_name}")
            except ClientError as e:
                print_log(f"  ⚠ Could not remove inline policy '{policy_name}': {e}", "WARNING")
        
        if not role_config['inline_policies'] and not current_inline_names:
            print_log(f"   No inline policies configured for {role_name}")
                
    except ClientError as e:
        print_log(f"   Error managing inline policies for {role_name}: {e}", "ERROR")

def instance_profile_exists(iam_client, profile_name: str) -> bool:
    """
    Check if an instance profile exists (idempotent check).
    
    Args:
        iam_client: boto3 IAM client
        profile_name: Name of the instance profile
        
    Returns:
        True if profile exists, False otherwise
    """
    try:
        iam_client.get_instance_profile(InstanceProfileName=profile_name)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return False
        raise

def create_and_attach_instance_profile(iam_client, profile_name: str, role_name: str) -> bool:
    """
    Create instance profile and attach role (idempotent).
    
    Args:
        iam_client: boto3 IAM client
        profile_name: Name of the instance profile
        role_name: Name of the IAM role to attach
        
    Returns:
        True if successful, False otherwise
    """
    profile_exists_flag = False
    role_attached = False
    
    try:
        # Check if instance profile exists
        if instance_profile_exists(iam_client, profile_name):
            existing_profile = iam_client.get_instance_profile(InstanceProfileName=profile_name)
            profile_exists_flag = True
            print_log(f" Instance profile {profile_name} already exists")
            
            # Check if correct role is attached
            roles_in_profile = existing_profile['InstanceProfile'].get('Roles', [])
            role_attached = any(role['RoleName'] == role_name for role in roles_in_profile)
            
            if role_attached:
                print_log(f"   Role {role_name} correctly attached")
                return True
            elif roles_in_profile:
                # Profile has a different role attached
                print_log(f"  ⚠ Instance profile already has role(s) attached: {[r['RoleName'] for r in roles_in_profile]}", "WARNING")
                print_log(f"  ⚠ Skipping attachment of {role_name} to avoid conflicts", "WARNING")
                return True
            else:
                # Profile exists but no role attached - could be intentionally removed
                print_log(f"  ⚠ Instance profile exists but has no role attached", "WARNING")
                print_log(f"  ⚠ Skipping re-attachment of {role_name} (may have been manually removed)", "WARNING")
                return True
        else:
            profile_exists_flag = False
        
        # Create profile if it doesn't exist
        if not profile_exists_flag:
            try:
                iam_client.create_instance_profile(InstanceProfileName=profile_name)
                print_log(f" Created instance profile: {profile_name}")
                profile_exists_flag = True
            except ClientError as create_error:
                print_log(f" Failed to create instance profile {profile_name}: {create_error}", "ERROR")
                return False
        
        # Attach role if not already attached
        if profile_exists_flag and not role_attached:
            try:
                iam_client.add_role_to_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role_name
                )
                print_log(f"   Attached role {role_name} to instance profile")
            except ClientError as attach_error:
                if attach_error.response['Error']['Code'] == 'LimitExceeded':
                    print_log(f"  ⚠ Role already attached", "WARNING")
                else:
                    print_log(f"   Failed to attach role: {attach_error}", "ERROR")
                    return False
        
        return True
        
    except ClientError as e:
        print_log(f" Failed to create instance profile {profile_name}: {e}", "ERROR")
        return False

def create_s3_bucket(region: str, s3_bucket: str, project_name: str, environment: str, managed_by: str) -> bool:
    """
    Create S3 bucket if it doesn't exist (idempotent).
    
    Args:
        region: AWS region
        s3_bucket: S3 bucket name
        project_name: Project name tag
        environment: Environment tag
        managed_by: Managed by tag
        
    Returns:
        True if successful, False otherwise
    """
    try:
        s3_client = boto3.client('s3', region_name=region)
        
        print_log(f"Ensuring S3 bucket '{s3_bucket}' exists...")
        
        # Check if bucket exists
        try:
            s3_client.head_bucket(Bucket=s3_bucket)
            print_log(f" S3 bucket {s3_bucket} already exists")
            
            # Verify bucket configuration
            try:
                # Check versioning status
                versioning = s3_client.get_bucket_versioning(Bucket=s3_bucket)
                if versioning.get('Status') != 'Enabled':
                    s3_client.put_bucket_versioning(
                        Bucket=s3_bucket,
                        VersioningConfiguration={'Status': 'Enabled'}
                    )
                    print_log(f"   Enabled versioning on bucket {s3_bucket}")
                else:
                    print_log(f"   Versioning already enabled on bucket {s3_bucket}")
                
                # Check encryption
                try:
                    s3_client.get_bucket_encryption(Bucket=s3_bucket)
                    print_log(f"   Encryption already configured on bucket {s3_bucket}")
                except ClientError as enc_error:
                    if enc_error.response['Error']['Code'] == 'ServerSideEncryptionConfigurationNotFoundError':
                        s3_client.put_bucket_encryption(
                            Bucket=s3_bucket,
                            ServerSideEncryptionConfiguration={
                                'Rules': [{
                                    'ApplyServerSideEncryptionByDefault': {
                                        'SSEAlgorithm': 'AES256'
                                    }
                                }]
                            }
                        )
                        print_log(f"   Enabled encryption on bucket {s3_bucket}")
                
                # Check tagging
                try:
                    tags_response = s3_client.get_bucket_tagging(Bucket=s3_bucket)
                    existing_tags = {tag['Key']: tag['Value'] for tag in tags_response.get('TagSet', [])}
                except ClientError:
                    existing_tags = {}
                
                desired_tags = {
                    'Project': project_name,
                    'Environment': environment,
                    'ManagedBy': managed_by
                }
                
                if existing_tags != desired_tags:
                    s3_client.put_bucket_tagging(
                        Bucket=s3_bucket,
                        Tagging={'TagSet': [{'Key': k, 'Value': v} for k, v in desired_tags.items()]}
                    )
                    print_log(f"   Updated tags on bucket {s3_bucket}")
                else:
                    print_log(f"   Tags already configured on bucket {s3_bucket}")
                
            except ClientError as config_error:
                print_log(f"⚠ Could not verify/update bucket configuration: {config_error}", "WARNING")
            
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] in ['404', 'NoSuchBucket']:
                try:
                    # Create bucket
                    if region == 'us-east-1':
                        s3_client.create_bucket(Bucket=s3_bucket)
                    else:
                        s3_client.create_bucket(
                            Bucket=s3_bucket,
                            CreateBucketConfiguration={'LocationConstraint': region}
                        )
                    
                    print_log(f" Created S3 bucket: {s3_bucket}")
                    
                    # Enable versioning
                    s3_client.put_bucket_versioning(
                        Bucket=s3_bucket,
                        VersioningConfiguration={'Status': 'Enabled'}
                    )
                    print_log(f"   Enabled versioning")
                    
                    # Enable encryption
                    s3_client.put_bucket_encryption(
                        Bucket=s3_bucket,
                        ServerSideEncryptionConfiguration={
                            'Rules': [{
                                'ApplyServerSideEncryptionByDefault': {
                                    'SSEAlgorithm': 'AES256'
                                }
                            }]
                        }
                    )
                    print_log(f"   Enabled encryption")
                    
                    # Add tags
                    s3_client.put_bucket_tagging(
                        Bucket=s3_bucket,
                        Tagging={
                            'TagSet': [
                                {'Key': 'Project', 'Value': project_name},
                                {'Key': 'Environment', 'Value': environment},
                                {'Key': 'ManagedBy', 'Value': managed_by}
                            ]
                        }
                    )
                    print_log(f"   Added tags")
                    
                    return True
                    
                except ClientError as create_error:
                    print_log(f" Failed to create S3 bucket: {create_error}", "ERROR")
                    return False
            else:
                print_log(f" Error checking S3 bucket: {e}", "ERROR")
                return False
        
    except Exception as e:
        print_log(f" Unexpected error with S3 bucket: {str(e)}", "ERROR")
        return False

def create_iam_resources(region: str, account_id: str, s3_bucket: str, connection_arn: str,
                        tomcat_role: str, jenkins_role: str, codedeploy_instance_role: str,
                        codedeploy_service_role: str, codebuild_service_role: str,
                        tg1_role: str, tg1_profile: str) -> bool:
    """
    Main function to create all IAM resources.
    
    Args:
        region: AWS region
        account_id: AWS account ID
        s3_bucket: S3 bucket name
        connection_arn: CodeConnections ARN
        tomcat_role: Tomcat role name
        jenkins_role: Jenkins role name
        codedeploy_instance_role: CodeDeploy instance role name
        codedeploy_service_role: CodeDeploy service role name
        codebuild_service_role: CodeBuild service role name
        tg1_role: TG1 role name
        tg1_profile: TG1 instance profile name
        
    Returns:
        True if successful, False otherwise
    """
    try:
        iam_client = boto3.client('iam', region_name=region)
        
        print_log("Starting IAM policies and roles configuration...")
        
        # Step 1: Create customer managed policies
        print_log("Step 1: Creating customer managed policies...")
        policies = get_policy_definitions(account_id, s3_bucket, connection_arn, region)
        
        for policy_name, policy_document in policies.items():
            if not create_or_update_policy(iam_client, policy_name, policy_document, account_id):
                return False
        
        # Step 2: Create IAM roles
        print_log("Step 2: Creating IAM roles...")
        roles = get_role_definitions(account_id, connection_arn, tomcat_role, jenkins_role,
                                   codedeploy_instance_role, codedeploy_service_role,
                                   codebuild_service_role, tg1_role, s3_bucket)
        
        for role_name, role_config in roles.items():
            if not create_or_update_role(iam_client, role_name, role_config, account_id):
                return False
        
        # Step 3: Create instance profiles
        print_log("Step 3: Creating instance profiles...")
        
        # Create instance profiles (profile name = role name for EC2 roles)
        ec2_roles = [tomcat_role, jenkins_role, codedeploy_instance_role]
        for role_name in ec2_roles:
            if not create_and_attach_instance_profile(iam_client, role_name, role_name):
                return False
        
        # Create TG1 instance profile (profile name != role name)
        if not create_and_attach_instance_profile(iam_client, tg1_profile, tg1_role):
            return False
        
        print_log(" All IAM resources configured successfully")
        return True
        
    except Exception as e:
        print_log(f" Error in IAM configuration: {str(e)}", "ERROR")
        return False

def orchestrate_iam_automation(region: str, account_id: str, s3_bucket: str, connection_arn: str,
                              tomcat_role: str, jenkins_role: str, codedeploy_instance_role: str,
                              codedeploy_service_role: str, codebuild_service_role: str,
                              tg1_role: str, tg1_profile: str, project_name: str, 
                              environment: str, managed_by: str) -> bool:
    """
    Main orchestration function that uses validators to check state, then services to fix gaps.
    
    This implements the enterprise-grade validate-then-fix pattern:
    1. Validate current AWS state using validators
    2. Identify what needs to be created/updated
    3. Use existing service functions to make only necessary changes
    4. Re-validate to ensure success
    
    Returns:
        True if all resources are correctly configured, False otherwise
    """
    print_log("=" * 60)
    print_log("IAM Automation Starting..")
    print_log("=" * 60)
    
    # Step 1: Validate AWS credentials (Pure Functional Approach)
    print_log("Step 1: Validating AWS credentials...")
    creds_status = validate_aws_credentials(region)
    
    # Fix: Handle boolean return type instead of dictionary
    if isinstance(creds_status, dict):
        if not creds_status.get('valid', False):
            print_log(f" {creds_status.get('message', 'AWS credentials validation failed')}", "ERROR")
            return False
        print_log(f" {creds_status.get('message', 'AWS credentials valid')}")
    else:
        # Handle boolean return (current case)
        if not creds_status:
            print_log(" AWS credentials validation failed", "ERROR")
            return False
        print_log(" AWS credentials validation successful")
    
    # Step 2: Validate S3 bucket (create if needed)
    print_log("Step 2: Validating S3 bucket configuration...")
    s3_config = {
        'tags': {
            'Project': project_name,
            'Environment': environment,
            'ManagedBy': managed_by
        }
    }
    s3_status = validate_s3_bucket_configuration(s3_bucket, s3_config, region)
    
    if not s3_status['exists']:
        print_log(f"S3 bucket {s3_bucket} does not exist, creating...")
        if not create_s3_bucket(region, s3_bucket, project_name, environment, managed_by):
            print_log(" S3 bucket creation failed", "ERROR")
            return False
    elif s3_status.get('needs_updates', False):
        print_log(f"S3 bucket {s3_bucket} needs configuration updates...")
        if not create_s3_bucket(region, s3_bucket, project_name, environment, managed_by):
            print_log(" S3 bucket update failed", "ERROR")
            return False
    else:
        print_log(f" {s3_status['message']}")
    
    # Step 3: Validate and ensure customer managed policies
    print_log("Step 3: Validating customer managed policies...")
    policy_definitions = get_policy_definitions(account_id, s3_bucket, connection_arn, region)
    policy_statuses = validate_all_policies(policy_definitions, account_id, region)
    
    policies_need_work = False
    for policy_name, status in policy_statuses.items():
        if not status['exists']:
            print_log(f"Policy {policy_name} does not exist, will create...")
            policies_need_work = True
        elif status.get('needs_update', False):
            print_log(f"Policy {policy_name} needs updating...")
            policies_need_work = True
        else:
            print_log(f" Policy {policy_name} is correctly configured")
    
    if policies_need_work:
        print_log("Creating/updating policies...")
        iam_client = boto3.client('iam', region_name=region)
        for policy_name, policy_document in policy_definitions.items():
            if not create_or_update_policy(iam_client, policy_name, policy_document, account_id):
                return False
    
    # Step 4: Validate and ensure IAM roles
    print_log("Step 4: Validating IAM roles...")
    role_definitions = get_role_definitions(account_id, connection_arn, tomcat_role, jenkins_role,
                                          codedeploy_instance_role, codedeploy_service_role,
                                          codebuild_service_role, tg1_role, s3_bucket)
    role_statuses = validate_all_roles(role_definitions, region)
    
    roles_need_work = False
    for role_name, status in role_statuses.items():
        if status['needs_creation']:
            print_log(f"Role {role_name} does not exist, will create...")
            roles_need_work = True
        elif status['needs_updates']:
            print_log(f"Role {role_name} needs configuration updates...")
            roles_need_work = True
        else:
            print_log(f" Role {role_name} is correctly configured")
    
    if roles_need_work:
        print_log("Creating/updating roles...")
        iam_client = boto3.client('iam', region_name=region)
        for role_name, role_config in role_definitions.items():
            if not create_or_update_role(iam_client, role_name, role_config, account_id):
                return False
    
    # Step 5: Validate and ensure instance profiles
    print_log("Step 5: Validating instance profiles...")
    profile_definitions = {
        tomcat_role: tomcat_role,
        jenkins_role: jenkins_role,
        codedeploy_instance_role: codedeploy_instance_role,
        tg1_profile: tg1_role
    }
    profile_statuses = validate_all_instance_profiles(profile_definitions, region)
    
    profiles_need_work = False
    for profile_name, status in profile_statuses.items():
        if not status['exists']:
            print_log(f"Instance profile {profile_name} does not exist, will create...")
            profiles_need_work = True
        elif status.get('needs_role_update', False):
            print_log(f"Instance profile {profile_name} needs role attachment update...")
            profiles_need_work = True
        else:
            print_log(f" Instance profile {profile_name} is correctly configured")
    
    if profiles_need_work:
        print_log("Creating/updating instance profiles...")
        iam_client = boto3.client('iam', region_name=region)
        for profile_name, role_name in profile_definitions.items():
            if not create_and_attach_instance_profile(iam_client, profile_name, role_name):
                return False
    
    # Step 6: Final validation
    print_log("Step 6: Performing final validation...")
    
    # Re-validate everything to ensure success
    final_policy_statuses = validate_all_policies(policy_definitions, account_id, region)
    final_role_statuses = validate_all_roles(role_definitions, region)
    final_profile_statuses = validate_all_instance_profiles(profile_definitions, region)
    final_s3_status = validate_s3_bucket_configuration(s3_bucket, s3_config, region)
    
    # Check if everything is now correct
    all_good = True
    
    for policy_name, status in final_policy_statuses.items():
        if not (status['exists'] and status.get('document_matches', True)):
            print_log(f" Policy {policy_name} still has issues", "ERROR")
            all_good = False
    
    for role_name, status in final_role_statuses.items():
        if status['needs_creation'] or status['needs_updates']:
            print_log(f" Role {role_name} still has issues", "ERROR")
            all_good = False
    
    for profile_name, status in final_profile_statuses.items():
        if not status['exists'] or not status.get('role_correct', True):
            print_log(f" Instance profile {profile_name} still has issues", "ERROR")
            all_good = False
    
    if not final_s3_status['exists'] or final_s3_status.get('needs_updates', False):
        print_log(f" S3 bucket {s3_bucket} still has issues", "ERROR")
        all_good = False
    
    # Step 7: Wait for IAM propagation if changes were made
    if policies_need_work or roles_need_work or profiles_need_work:
        print_log("=" * 60)
        print_log("Step 7: Waiting for IAM propagation...")
        print_log("IAM changes need 30-60 seconds to propagate globally")
        print_log("This ensures CodeBuild/CodeDeploy can use the new permissions")
        print_log("=" * 60)
        
        import time
        wait_time = 45  # 45 seconds is a good balance
        
        for remaining in range(wait_time, 0, -5):
            print_log(f"Waiting... {remaining} seconds remaining", flush=True)
            time.sleep(5)
        
        print_log(" IAM propagation wait completed")
    else:
        print_log("No IAM changes made, skipping propagation wait")
    
    return all_good


def main():
    """
    Main function - accepts CLI arguments passed from main.sh.
    Same interface as before, but now uses enterprise-grade validation internally.
    """
    parser = argparse.ArgumentParser(description='IAM Automation Script - Enterprise Idempotent Mode')
    parser.add_argument('region', help='AWS region')
    parser.add_argument('account_id', help='AWS account ID')
    parser.add_argument('s3_bucket', help='S3 bucket name')
    parser.add_argument('connection_arn', help='CodeConnections ARN')
    parser.add_argument('tomcat_role', help='Tomcat role name')
    parser.add_argument('jenkins_role', help='Jenkins role name')
    parser.add_argument('codedeploy_instance_role', help='CodeDeploy instance role name')
    parser.add_argument('codedeploy_service_role', help='CodeDeploy service role name')
    parser.add_argument('codebuild_service_role', help='CodeBuild service role name')
    parser.add_argument('tg1_role', help='TG1 role name')
    parser.add_argument('tg1_profile', help='TG1 instance profile name')
    parser.add_argument('project_name', help='Project name for tagging')
    parser.add_argument('environment', help='Environment for tagging')
    parser.add_argument('managed_by', help='Managed by for tagging')
    
    args = parser.parse_args()
    
    # Use new orchestration function
    success = orchestrate_iam_automation(
        args.region, args.account_id, args.s3_bucket, args.connection_arn,
        args.tomcat_role, args.jenkins_role, args.codedeploy_instance_role,
        args.codedeploy_service_role, args.codebuild_service_role,
        args.tg1_role, args.tg1_profile, args.project_name, 
        args.environment, args.managed_by
    )
    
    if success:
        print_log("=" * 60)
        print_log(" All IAM resources validated and correctly configured")
        print_log(" IAM Automation completed successfully")
        print_log("=" * 60)
        sys.exit(0)  # Success exit code
    else:
        print_log("=" * 60)
        print_log(" IAM automation failed - some resources could not be configured correctly", "ERROR")
        print_log("=" * 60)
        sys.exit(1)  # Failure exit code

if __name__ == "__main__":
    main()