#!/usr/bin/env python3
"""
AWS IAM and S3 Validators

This module provides validation functions for AWS IAM and S3 resources following 
the idempotent pattern for safe multiple executions.

Key Principles:
- READ-ONLY validation operations
- State checking and drift detection
- Actionable feedback for services
- Functional programming approach
"""

import boto3
import json
from datetime import datetime
from botocore.exceptions import ClientError, NoCredentialsError
from typing import Dict, List, Optional, Tuple, Any


def validate_aws_infrastructure(
    iam_config: Dict[str, Any], 
    s3_config: Dict[str, Any], 
    region: str
) -> Dict[str, Any]:
    """
    Validate AWS IAM and S3 infrastructure state.
    Performs state checking and provides remediation guidance.
    
    Args:
        iam_config: Complete IAM configuration (policies, roles, instance profiles)
        s3_config: Complete S3 configuration (buckets with settings)
        region: AWS region
    
    Returns:
        Dict with complete validation results and remediation actions needed
    """
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Starting AWS infrastructure validation")
    
    validation_results = {
        'validation_timestamp': datetime.now().isoformat(),
        'region': region,
        'overall_status': 'UNKNOWN',
        'requires_remediation': False,
        'credentials': {},
        'iam_resources': {},
        's3_resources': {},
        'remediation_summary': {
            'resources_to_create': [],
            'resources_to_update': [],
            'resources_to_sync': []
        }
    }
    
    # Step 1: Validate AWS credentials (prerequisite)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating AWS credentials")
    credentials_result = validate_aws_credentials(region)
    validation_results['credentials'] = credentials_result
    
    if not credentials_result['valid']:
        validation_results['overall_status'] = 'FAILED'
        validation_results['requires_remediation'] = True
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] AWS credentials validation failed")
        return validation_results
    
    account_id = credentials_result['account_id']
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Credentials valid for account: {account_id}")
    
    # Step 2: Validate IAM Infrastructure
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating IAM infrastructure")
    iam_results = validate_iam_infrastructure(iam_config, account_id, region)
    validation_results['iam_resources'] = iam_results
    
    # Step 3: Validate S3 Infrastructure  
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating S3 infrastructure")
    s3_results = validate_s3_infrastructure(s3_config, region)
    validation_results['s3_resources'] = s3_results
    
    # Step 4: Generate remediation summary
    remediation_summary = generate_remediation_summary(iam_results, s3_results)
    validation_results['remediation_summary'] = remediation_summary
    validation_results['requires_remediation'] = remediation_summary['total_actions'] > 0
    
    # Step 5: Determine overall status
    if validation_results['requires_remediation']:
        validation_results['overall_status'] = 'DRIFT_DETECTED'
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WARNING] Configuration drift detected - remediation required")
    else:
        validation_results['overall_status'] = 'COMPLIANT' 
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] All infrastructure is compliant")
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validation completed")
    return validation_results


def validate_iam_infrastructure(iam_config: Dict[str, Any], account_id: str, region: str) -> Dict[str, Any]:
    """
    Validate IAM infrastructure state.
    
    Args:
        iam_config: Complete IAM configuration 
        account_id: AWS account ID
        region: AWS region
    
    Returns:
        Dict with IAM validation results
    """
    results = {
        'validation_type': 'IAM_INFRASTRUCTURE',
        'policies': {},
        'roles': {}, 
        'instance_profiles': {},
        'summary': {
            'total_policies': 0,
            'policies_needing_creation': 0,
            'policies_needing_updates': 0,
            'total_roles': 0,
            'roles_needing_creation': 0,
            'roles_needing_updates': 0,
            'total_instance_profiles': 0,
            'profiles_needing_creation': 0,
            'profiles_needing_updates': 0
        }
    }
    
    # Validate customer managed policies
    if 'policies' in iam_config:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating {len(iam_config['policies'])} IAM policies")
        results['policies'] = validate_all_policies(iam_config['policies'], account_id, region)
        results['summary']['total_policies'] = len(iam_config['policies'])
        
        for policy_name, policy_result in results['policies'].items():
            if policy_result.get('needs_creation', False):
                results['summary']['policies_needing_creation'] += 1
            elif policy_result.get('needs_update', False):
                results['summary']['policies_needing_updates'] += 1
    
    # Validate IAM roles
    if 'roles' in iam_config:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating {len(iam_config['roles'])} IAM roles")
        results['roles'] = validate_all_roles(iam_config['roles'], region)
        results['summary']['total_roles'] = len(iam_config['roles'])
        
        for role_name, role_result in results['roles'].items():
            if role_result.get('needs_creation', False):
                results['summary']['roles_needing_creation'] += 1
            elif role_result.get('needs_updates', False):
                results['summary']['roles_needing_updates'] += 1
    
    # Validate instance profiles
    if 'instance_profiles' in iam_config:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating {len(iam_config['instance_profiles'])} instance profiles")
        results['instance_profiles'] = validate_all_instance_profiles(iam_config['instance_profiles'], region)
        results['summary']['total_instance_profiles'] = len(iam_config['instance_profiles'])
        
        for profile_name, profile_result in results['instance_profiles'].items():
            if profile_result.get('needs_creation', False):
                results['summary']['profiles_needing_creation'] += 1
            elif profile_result.get('needs_role_update', False):
                results['summary']['profiles_needing_updates'] += 1
    
    return results


def validate_s3_infrastructure(s3_config: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Validate S3 infrastructure state.
    
    Args:
        s3_config: Complete S3 configuration
        region: AWS region
    
    Returns:
        Dict with S3 validation results
    """
    results = {
        'validation_type': 'S3_INFRASTRUCTURE',
        'buckets': {},
        'summary': {
            'total_buckets': 0,
            'buckets_needing_creation': 0,
            'buckets_needing_updates': 0
        }
    }
    
    if 'buckets' in s3_config:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Validating {len(s3_config['buckets'])} S3 buckets")
        results['summary']['total_buckets'] = len(s3_config['buckets'])
        
        for bucket_name, bucket_config in s3_config['buckets'].items():
            bucket_result = validate_s3_bucket_configuration(bucket_name, bucket_config, region)
            results['buckets'][bucket_name] = bucket_result
            
            if bucket_result.get('needs_creation', False):
                results['summary']['buckets_needing_creation'] += 1
            elif bucket_result.get('needs_updates', False):
                results['summary']['buckets_needing_updates'] += 1
    
    return results


def generate_remediation_summary(iam_results: Dict[str, Any], s3_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate remediation summary for infrastructure.
    
    Args:
        iam_results: IAM validation results
        s3_results: S3 validation results
    
    Returns:
        Dict with remediation actions summary
    """
    summary = {
        'total_actions': 0,
        'resources_to_create': [],
        'resources_to_update': [], 
        'resources_to_sync': [],
        'iam_actions': {
            'policies_to_create': [],
            'policies_to_update': [],
            'roles_to_create': [],
            'roles_to_update': [],
            'profiles_to_create': [],
            'profiles_to_sync': []
        },
        's3_actions': {
            'buckets_to_create': [],
            'buckets_to_update': []
        }
    }
    
    # Process IAM remediation needs
    if 'policies' in iam_results:
        for policy_name, policy_result in iam_results['policies'].items():
            if policy_result.get('needs_creation', False):
                summary['iam_actions']['policies_to_create'].append(policy_name)
                summary['resources_to_create'].append(f"IAM Policy: {policy_name}")
            elif policy_result.get('needs_update', False):
                summary['iam_actions']['policies_to_update'].append(policy_name)
                summary['resources_to_update'].append(f"IAM Policy: {policy_name}")
    
    if 'roles' in iam_results:
        for role_name, role_result in iam_results['roles'].items():
            if role_result.get('needs_creation', False):
                summary['iam_actions']['roles_to_create'].append(role_name)
                summary['resources_to_create'].append(f"IAM Role: {role_name}")
            elif role_result.get('needs_updates', False):
                summary['iam_actions']['roles_to_update'].append(role_name)
                summary['resources_to_sync'].append(f"IAM Role: {role_name}")
    
    if 'instance_profiles' in iam_results:
        for profile_name, profile_result in iam_results['instance_profiles'].items():
            if profile_result.get('needs_creation', False):
                summary['iam_actions']['profiles_to_create'].append(profile_name)
                summary['resources_to_create'].append(f"Instance Profile: {profile_name}")
            elif profile_result.get('needs_role_update', False):
                summary['iam_actions']['profiles_to_sync'].append(profile_name)
                summary['resources_to_sync'].append(f"Instance Profile: {profile_name}")
    
    # Process S3 remediation needs
    if 'buckets' in s3_results:
        for bucket_name, bucket_result in s3_results['buckets'].items():
            if bucket_result.get('needs_creation', False):
                summary['s3_actions']['buckets_to_create'].append(bucket_name)
                summary['resources_to_create'].append(f"S3 Bucket: {bucket_name}")
            elif bucket_result.get('needs_updates', False):
                summary['s3_actions']['buckets_to_update'].append(bucket_name)
                summary['resources_to_update'].append(f"S3 Bucket: {bucket_name}")
    
    # Calculate total actions needed
    summary['total_actions'] = (
        len(summary['resources_to_create']) +
        len(summary['resources_to_update']) + 
        len(summary['resources_to_sync'])
    )
    
    return summary


def validate_aws_credentials(region: str) -> Dict[str, Any]:
    """
    Validate AWS credentials and return account info.
    
    Args:
        region: AWS region
        
    Returns:
        Dict with validation status and account information
    """
    try:
        sts_client = boto3.client('sts', region_name=region)
        identity = sts_client.get_caller_identity()
        return {
            'valid': True,
            'account_id': identity['Account'],
            'user_id': identity['UserId'],
            'arn': identity['Arn'],
            'message': f"Credentials valid - Running as: {identity['Arn']}"
        }
    except NoCredentialsError:
        return {
            'valid': False,
            'message': 'AWS credentials not configured!'
        }
    except Exception as e:
        return {
            'valid': False,
            'message': f'Error validating AWS credentials: {str(e)}'
        }


def validate_policy_exists(policy_name: str, account_id: str, region: str) -> Dict[str, Any]:
    """
    Check if a customer managed policy exists.
    
    Args:
        policy_name: Name of the policy
        account_id: AWS account ID
        region: AWS region
        
    Returns:
        Dict with existence status and policy details
    """
    try:
        iam_client = boto3.client('iam', region_name=region)
        policy_arn = f'arn:aws:iam::{account_id}:policy/{policy_name}'
        policy_response = iam_client.get_policy(PolicyArn=policy_arn)
        
        return {
            'exists': True,
            'policy_arn': policy_arn,
            'policy_id': policy_response['Policy']['PolicyId'],
            'default_version_id': policy_response['Policy']['DefaultVersionId'],
            'attachment_count': policy_response['Policy']['AttachmentCount'],
            'permissions_boundary_usage_count': policy_response['Policy']['PermissionsBoundaryUsageCount'],
            'create_date': policy_response['Policy']['CreateDate'],
            'update_date': policy_response['Policy']['UpdateDate']
        }
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return {
                'exists': False,
                'policy_arn': f'arn:aws:iam::{account_id}:policy/{policy_name}',
                'message': f'Policy {policy_name} does not exist'
            }
        return {
            'exists': False,
            'error': str(e),
            'message': f'Error checking policy {policy_name}: {e}'
        }


def validate_policy_document(policy_name: str, expected_document: Dict[str, Any], 
                            account_id: str, region: str) -> Dict[str, Any]:
    """
    Validate policy document matches expected configuration.
    
    Args:
        policy_name: Name of the policy
        expected_document: Expected policy document
        account_id: AWS account ID
        region: AWS region
        
    Returns:
        Dict with validation status and differences
    """
    policy_status = validate_policy_exists(policy_name, account_id, region)
    
    if not policy_status['exists']:
        return {
            'exists': False,
            'document_matches': False,
            'needs_creation': True,
            'message': f'Policy {policy_name} does not exist and needs creation'
        }
    
    try:
        iam_client = boto3.client('iam', region_name=region)
        # Get current policy document
        policy_arn = policy_status['policy_arn']
        version_response = iam_client.get_policy_version(
            PolicyArn=policy_arn,
            VersionId=policy_status['default_version_id']
        )
        
        current_document = version_response['PolicyVersion']['Document']
        document_matches = current_document == expected_document
        
        return {
            'exists': True,
            'document_matches': document_matches,
            'needs_update': not document_matches,
            'current_document': current_document,
            'expected_document': expected_document,
            'message': f'Policy {policy_name} exists, document matches: {document_matches}'
        }
        
    except ClientError as e:
        return {
            'exists': True,
            'document_matches': False,
            'error': str(e),
            'message': f'Error validating policy document for {policy_name}: {e}'
        }


def validate_all_policies(policy_definitions: Dict[str, Dict[str, Any]], 
                         account_id: str, region: str) -> Dict[str, Dict[str, Any]]:
    """
    Validate all customer managed policies.
    
    Args:
        policy_definitions: Dictionary of policy names and their expected documents
        account_id: AWS account ID
        region: AWS region
        
    Returns:
        Dict with validation status for each policy
    """
    results = {}
    
    for policy_name, policy_document in policy_definitions.items():
        results[policy_name] = validate_policy_document(
            policy_name, policy_document, account_id, region
        )
    
    return results


def validate_role_exists(role_name: str, region: str) -> Dict[str, Any]:
    """
    Check if an IAM role exists.
    
    Args:
        role_name: Name of the role
        region: AWS region
        
    Returns:
        Dict with existence status and role details
    """
    try:
        iam_client = boto3.client('iam', region_name=region)
        role_response = iam_client.get_role(RoleName=role_name)
        role = role_response['Role']
        
        return {
            'exists': True,
            'role_id': role['RoleId'],
            'arn': role['Arn'],
            'create_date': role['CreateDate'],
            'assume_role_policy_document': role['AssumeRolePolicyDocument'],
            'description': role.get('Description', ''),
            'max_session_duration': role.get('MaxSessionDuration', 3600)
        }
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return {
                'exists': False,
                'message': f'Role {role_name} does not exist'
            }
        return {
            'exists': False,
            'error': str(e),
            'message': f'Error checking role {role_name}: {e}'
        }


def validate_role_assume_policy(role_name: str, expected_assume_policy: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Validate role's assume role policy document.
    
    Args:
        role_name: Name of the role
        expected_assume_policy: Expected assume role policy document
        region: AWS region
        
    Returns:
        Dict with validation status
    """
    role_status = validate_role_exists(role_name, region)
    
    if not role_status['exists']:
        return {
            'exists': False,
            'assume_policy_matches': False,
            'needs_creation': True,
            'message': f'Role {role_name} does not exist'
        }
    
    current_policy = role_status['assume_role_policy_document']
    policy_matches = current_policy == expected_assume_policy
    
    return {
        'exists': True,
        'assume_policy_matches': policy_matches,
        'needs_update': not policy_matches,
        'current_policy': current_policy,
        'expected_policy': expected_assume_policy,
        'message': f'Role {role_name} assume policy matches: {policy_matches}'
    }


def validate_role_attached_policies(role_name: str, expected_policies: List[str], region: str) -> Dict[str, Any]:
    """
    Validate role's attached managed policies.
    
    Args:
        role_name: Name of the role
        expected_policies: List of expected policy ARNs
        region: AWS region
        
    Returns:
        Dict with validation status and policy differences
    """
    role_status = validate_role_exists(role_name, region)
    
    if not role_status['exists']:
        return {
            'exists': False,
            'policies_match': False,
            'needs_creation': True,
            'message': f'Role {role_name} does not exist'
        }
    
    try:
        iam_client = boto3.client('iam', region_name=region)
        # Get currently attached policies
        attached_response = iam_client.list_attached_role_policies(RoleName=role_name)
        current_policies = {p['PolicyArn'] for p in attached_response['AttachedPolicies']}
        expected_policies_set = set(expected_policies)
        
        policies_match = current_policies == expected_policies_set
        missing_policies = expected_policies_set - current_policies
        extra_policies = current_policies - expected_policies_set
        
        return {
            'exists': True,
            'policies_match': policies_match,
            'needs_sync': not policies_match,
            'current_policies': list(current_policies),
            'expected_policies': expected_policies,
            'missing_policies': list(missing_policies),
            'extra_policies': list(extra_policies),
            'message': f'Role {role_name} attached policies match: {policies_match}'
        }
        
    except ClientError as e:
        return {
            'exists': True,
            'policies_match': False,
            'error': str(e),
            'message': f'Error validating attached policies for {role_name}: {e}'
        }


def validate_role_inline_policies(role_name: str, expected_policies: Dict[str, Dict[str, Any]], region: str) -> Dict[str, Any]:
    """
    Validate role's inline policies.
    
    Args:
        role_name: Name of the role
        expected_policies: Dict of policy names and their expected documents
        region: AWS region
        
    Returns:
        Dict with validation status and policy differences
    """
    role_status = validate_role_exists(role_name, region)
    
    if not role_status['exists']:
        return {
            'exists': False,
            'inline_policies_match': False,
            'needs_creation': True,
            'message': f'Role {role_name} does not exist'
        }
    
    try:
        iam_client = boto3.client('iam', region_name=region)
        # Get current inline policies
        inline_response = iam_client.list_role_policies(RoleName=role_name)
        current_policy_names = set(inline_response['PolicyNames'])
        expected_policy_names = set(expected_policies.keys())
        
        policies_match = True
        policy_details = {}
        
        # Check each expected policy
        for policy_name, expected_doc in expected_policies.items():
            if policy_name in current_policy_names:
                # Policy exists, check document
                policy_response = iam_client.get_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name
                )
                current_doc = policy_response['PolicyDocument']
                doc_matches = current_doc == expected_doc
                
                policy_details[policy_name] = {
                    'exists': True,
                    'document_matches': doc_matches,
                    'current_document': current_doc,
                    'expected_document': expected_doc
                }
                
                if not doc_matches:
                    policies_match = False
            else:
                # Policy missing
                policy_details[policy_name] = {
                    'exists': False,
                    'document_matches': False,
                    'expected_document': expected_doc
                }
                policies_match = False
        
        # Check for extra policies
        extra_policies = current_policy_names - expected_policy_names
        if extra_policies:
            policies_match = False
        
        return {
            'exists': True,
            'inline_policies_match': policies_match,
            'needs_sync': not policies_match,
            'policy_details': policy_details,
            'extra_policies': list(extra_policies),
            'message': f'Role {role_name} inline policies match: {policies_match}'
        }
        
    except ClientError as e:
        return {
            'exists': True,
            'inline_policies_match': False,
            'error': str(e),
            'message': f'Error validating inline policies for {role_name}: {e}'
        }


def validate_complete_role(role_name: str, role_config: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Validate a role including assume policy, attached policies, and inline policies.
    
    Args:
        role_name: Name of the role
        role_config: Complete role configuration
        region: AWS region
        
    Returns:
        Dict with validation results
    """
    results = {
        'role_name': role_name,
        'needs_creation': False,
        'needs_updates': False,
        'validation_details': {}
    }
    
    # Check role existence
    existence_result = validate_role_exists(role_name, region)
    results['validation_details']['existence'] = existence_result
    
    if not existence_result['exists']:
        results['needs_creation'] = True
        results['message'] = f'Role {role_name} needs creation'
        return results
    
    # Validate assume role policy
    assume_policy_result = validate_role_assume_policy(
        role_name, role_config['assume_role_policy'], region
    )
    results['validation_details']['assume_policy'] = assume_policy_result
    if assume_policy_result.get('needs_update', False):
        results['needs_updates'] = True
    
    # Validate attached managed policies
    attached_policies_result = validate_role_attached_policies(
        role_name, role_config['managed_policies'], region
    )
    results['validation_details']['attached_policies'] = attached_policies_result
    if attached_policies_result.get('needs_sync', False):
        results['needs_updates'] = True
    
    # Validate inline policies
    inline_policies_result = validate_role_inline_policies(
        role_name, role_config['inline_policies'], region
    )
    results['validation_details']['inline_policies'] = inline_policies_result
    if inline_policies_result.get('needs_sync', False):
        results['needs_updates'] = True
    
    # Overall status
    if results['needs_updates']:
        results['message'] = f'Role {role_name} exists but needs updates'
    else:
        results['message'] = f'Role {role_name} is correctly configured'
    
    return results


def validate_all_roles(role_definitions: Dict[str, Dict[str, Any]], region: str) -> Dict[str, Dict[str, Any]]:
    """
    Validate all IAM roles.
    
    Args:
        role_definitions: Dictionary of role names and their configurations
        region: AWS region
        
    Returns:
        Dict with validation results for each role
    """
    results = {}
    
    for role_name, role_config in role_definitions.items():
        results[role_name] = validate_complete_role(role_name, role_config, region)
    
    return results


def validate_instance_profile_exists(profile_name: str, region: str) -> Dict[str, Any]:
    """
    Check if an instance profile exists.
    
    Args:
        profile_name: Name of the instance profile
        region: AWS region
        
    Returns:
        Dict with existence status and profile details
    """
    try:
        iam_client = boto3.client('iam', region_name=region)
        profile_response = iam_client.get_instance_profile(InstanceProfileName=profile_name)
        profile = profile_response['InstanceProfile']
        
        return {
            'exists': True,
            'instance_profile_id': profile['InstanceProfileId'],
            'arn': profile['Arn'],
            'create_date': profile['CreateDate'],
            'roles': [role['RoleName'] for role in profile.get('Roles', [])],
            'role_count': len(profile.get('Roles', []))
        }
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return {
                'exists': False,
                'message': f'Instance profile {profile_name} does not exist'
            }
        return {
            'exists': False,
            'error': str(e),
            'message': f'Error checking instance profile {profile_name}: {e}'
        }


def validate_instance_profile_role(profile_name: str, expected_role: str, region: str) -> Dict[str, Any]:
    """
    Validate instance profile has the correct role attached.
    
    Args:
        profile_name: Name of the instance profile
        expected_role: Expected role name
        region: AWS region
        
    Returns:
        Dict with validation status
    """
    profile_status = validate_instance_profile_exists(profile_name, region)
    
    if not profile_status['exists']:
        return {
            'exists': False,
            'role_correct': False,
            'needs_creation': True,
            'message': f'Instance profile {profile_name} does not exist'
        }
    
    current_roles = profile_status['roles']
    role_correct = len(current_roles) == 1 and current_roles[0] == expected_role
    
    return {
        'exists': True,
        'role_correct': role_correct,
        'needs_role_update': not role_correct,
        'current_roles': current_roles,
        'expected_role': expected_role,
        'message': f'Instance profile {profile_name} has correct role: {role_correct}'
    }


def validate_all_instance_profiles(profile_definitions: Dict[str, str], region: str) -> Dict[str, Dict[str, Any]]:
    """
    Validate all instance profiles.
    
    Args:
        profile_definitions: Dict of profile names and their expected role names
        region: AWS region
        
    Returns:
        Dict with validation results for each instance profile
    """
    results = {}
    
    for profile_name, expected_role in profile_definitions.items():
        results[profile_name] = validate_instance_profile_role(profile_name, expected_role, region)
    
    return results


def validate_s3_bucket_exists(bucket_name: str, region: str) -> Dict[str, Any]:
    """
    Check if S3 bucket exists.
    
    Args:
        bucket_name: Name of the S3 bucket
        region: AWS region
        
    Returns:
        Dict with existence status
    """
    try:
        s3_client = boto3.client('s3', region_name=region)
        s3_client.head_bucket(Bucket=bucket_name)
        return {
            'exists': True,
            'message': f'S3 bucket {bucket_name} exists'
        }
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code in ['404', 'NoSuchBucket']:
            return {
                'exists': False,
                'message': f'S3 bucket {bucket_name} does not exist'
            }
        return {
            'exists': False,
            'error': str(e),
            'message': f'Error checking S3 bucket {bucket_name}: {e}'
        }


def validate_s3_bucket_configuration(bucket_name: str, expected_config: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Validate S3 bucket configuration (versioning, encryption, tags).
    
    Args:
        bucket_name: Name of the S3 bucket
        expected_config: Expected configuration including tags
        region: AWS region
        
    Returns:
        Dict with validation results
    """
    bucket_status = validate_s3_bucket_exists(bucket_name, region)
    
    if not bucket_status['exists']:
        return {
            'exists': False,
            'configuration_correct': False,
            'needs_creation': True,
            'message': f'S3 bucket {bucket_name} does not exist'
        }
    
    validation_results = {
        'exists': True,
        'configuration_correct': True,
        'needs_updates': False,
        'details': {}
    }
    
    try:
        s3_client = boto3.client('s3', region_name=region)
        # Check versioning
        versioning_response = s3_client.get_bucket_versioning(Bucket=bucket_name)
        versioning_enabled = versioning_response.get('Status') == 'Enabled'
        validation_results['details']['versioning'] = {
            'enabled': versioning_enabled,
            'expected': True,
            'correct': versioning_enabled
        }
        if not versioning_enabled:
            validation_results['configuration_correct'] = False
            validation_results['needs_updates'] = True
        
        # Check encryption
        encryption_configured = False
        try:
            s3_client.get_bucket_encryption(Bucket=bucket_name)
            encryption_configured = True
        except ClientError as e:
            if e.response['Error']['Code'] != 'ServerSideEncryptionConfigurationNotFoundError':
                raise e
        
        validation_results['details']['encryption'] = {
            'configured': encryption_configured,
            'expected': True,
            'correct': encryption_configured
        }
        if not encryption_configured:
            validation_results['configuration_correct'] = False
            validation_results['needs_updates'] = True
        
        # Check tags
        try:
            tags_response = s3_client.get_bucket_tagging(Bucket=bucket_name)
            current_tags = {tag['Key']: tag['Value'] for tag in tags_response.get('TagSet', [])}
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchTagSet':
                current_tags = {}
            else:
                raise e
        
        expected_tags = expected_config.get('tags', {})
        tags_correct = current_tags == expected_tags
        validation_results['details']['tags'] = {
            'current': current_tags,
            'expected': expected_tags,
            'correct': tags_correct
        }
        if not tags_correct:
            validation_results['configuration_correct'] = False
            validation_results['needs_updates'] = True
        
    except ClientError as e:
        validation_results['configuration_correct'] = False
        validation_results['error'] = str(e)
        validation_results['message'] = f'Error validating S3 bucket configuration: {e}'
        return validation_results
    
    # Set overall message
    if validation_results['configuration_correct']:
        validation_results['message'] = f'S3 bucket {bucket_name} is correctly configured'
    else:
        validation_results['message'] = f'S3 bucket {bucket_name} exists but needs configuration updates'
    
    return validation_results


# Main validation interface - Pure Functional Approach
def validate_infrastructure_state(iam_config: Dict[str, Any], s3_config: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Main entry point for infrastructure validation.
    
    This function implements the idempotent validation pattern:
    1. Check current state of all resources
    2. Compare against desired configuration  
    3. Report drift and remediation actions needed
    4. Safe for multiple executions    Usage Example:
        config = {
            'iam': {
                'policies': {...},
                'roles': {...}, 
                'instance_profiles': {...}
            },
            's3': {
                'buckets': {...}
            }
        }
        
        result = validate_infrastructure_state(config['iam'], config['s3'], 'us-east-1')
        
        if result['requires_remediation']:
            print("Actions required:")
            for action in result['remediation_summary']['resources_to_create']:
                print(f"  Create: {action}")
    
    Args:
        iam_config: Complete IAM configuration
        s3_config: Complete S3 configuration  
        region: AWS region
    
    Returns:
        Validation results with remediation guidance
    """
    return validate_aws_infrastructure(iam_config, s3_config, region)


def validate_network_infrastructure(ec2_client, vpc_name, vpc_cidr, subnet_name, 
                                   subnet_cidr, igw_name, rt_name):
    """
    Validate network infrastructure created by shell scripts
    
    Args:
        ec2_client: boto3 EC2 client
        vpc_name: VPC name tag
        vpc_cidr: Expected VPC CIDR
        subnet_name: Subnet name tag
        subnet_cidr: Expected subnet CIDR
        igw_name: Internet Gateway name tag
        rt_name: Route table name tag
        
    Returns:
        tuple: (vpc_id, subnet_id, igw_id, rt_id)
    """
    def print_log(message, level="INFO"):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {message}", flush=True)
    
    try:
        # Validate VPC
        vpc_response = ec2_client.describe_vpcs(
            Filters=[{'Name': 'tag:Name', 'Values': [vpc_name]}]
        )
        
        if not vpc_response['Vpcs']:
            print_log(f"VPC '{vpc_name}' not found", "ERROR")
            raise ValueError(f"VPC '{vpc_name}' not found")
        
        vpc = vpc_response['Vpcs'][0]
        vpc_id = vpc['VpcId']
        found_cidr = vpc['CidrBlock']
        
        if found_cidr != vpc_cidr:
            print_log(f"VPC CIDR mismatch: expected {vpc_cidr}, found {found_cidr}", "ERROR")
            raise ValueError(f"VPC CIDR mismatch")
        
        print_log(f"VPC validated: {vpc_id} ({found_cidr})")
        
        # Validate Subnet
        subnet_response = ec2_client.describe_subnets(
            Filters=[
                {'Name': 'tag:Name', 'Values': [subnet_name]},
                {'Name': 'vpc-id', 'Values': [vpc_id]}
            ]
        )
        
        if not subnet_response['Subnets']:
            print_log(f"Subnet '{subnet_name}' not found in VPC {vpc_id}", "ERROR")
            raise ValueError(f"Subnet '{subnet_name}' not found")
        
        subnet = subnet_response['Subnets'][0]
        subnet_id = subnet['SubnetId']
        found_subnet_cidr = subnet['CidrBlock']
        
        if found_subnet_cidr != subnet_cidr:
            print_log(f"Subnet CIDR mismatch: expected {subnet_cidr}, found {found_subnet_cidr}", "ERROR")
            raise ValueError(f"Subnet CIDR mismatch")
        
        print_log(f"Subnet validated: {subnet_id} ({found_subnet_cidr})")
        
        # Validate Internet Gateway
        igw_response = ec2_client.describe_internet_gateways(
            Filters=[{'Name': 'tag:Name', 'Values': [igw_name]}]
        )
        
        if not igw_response['InternetGateways']:
            print_log(f"Internet Gateway '{igw_name}' not found", "ERROR")
            raise ValueError(f"Internet Gateway '{igw_name}' not found")
        
        igw = igw_response['InternetGateways'][0]
        igw_id = igw['InternetGatewayId']
        
        # Check if IGW is attached to VPC
        attachments = igw.get('Attachments', [])
        if not any(att['VpcId'] == vpc_id for att in attachments):
            print_log(f"Internet Gateway not attached to VPC {vpc_id}", "ERROR")
            raise ValueError(f"IGW not attached to VPC")
        
        print_log(f"Internet Gateway validated: {igw_id}")
        
        # Validate Route Table
        rt_response = ec2_client.describe_route_tables(
            Filters=[
                {'Name': 'tag:Name', 'Values': [rt_name]},
                {'Name': 'vpc-id', 'Values': [vpc_id]}
            ]
        )
        
        if not rt_response['RouteTables']:
            print_log(f"Route table '{rt_name}' not found in VPC {vpc_id}", "ERROR")
            raise ValueError(f"Route table '{rt_name}' not found")
        
        rt = rt_response['RouteTables'][0]
        rt_id = rt['RouteTableId']
        
        print_log(f"Route table validated: {rt_id}")
        print_log(f" Network infrastructure validation successful")
        
        return vpc_id, subnet_id, igw_id, rt_id
        
    except Exception as e:
        print_log(f"Network infrastructure validation failed: {e}", "ERROR")
        raise


def verify_codestar_connection(codestar_client, connection_name, region):
    """
    Verify CodeStar (CodeConnections) connection is available
    
    Args:
        codestar_client: boto3 CodeStar Connections client
        connection_name: Connection name
        region: AWS region
        
    Returns:
        str: Connection ARN if available, None otherwise
    """
    def print_log(message, level="INFO"):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {message}", flush=True)
    
    try:
        response = codestar_client.list_connections()
        
        for connection in response.get('Connections', []):
            if connection['ConnectionName'] == connection_name:
                status = connection['ConnectionStatus']
                arn = connection['ConnectionArn']
                
                print_log(f"Found connection '{connection_name}': {arn}")
                print_log(f"Connection status: {status}")
                
                if status == 'AVAILABLE':
                    print_log(f" CodeStar connection is ready")
                    return arn
                else:
                    print_log(f"Connection not available (status: {status})", "WARNING")
                    return None
        
        print_log(f"Connection '{connection_name}' not found", "WARNING")
        return None
        
    except Exception as e:
        print_log(f"Error checking CodeStar connection: {e}", "ERROR")
        return None


def validate_iam_resources(iam_client, s3_client, jenkins_role, tomcat_role,
                          codedeploy_instance_role, codedeploy_service_role,
                          codebuild_service_role, s3_bucket, region):
    """
    Validate IAM resources exist (created by iam_service.py)
    
    Args:
        iam_client: boto3 IAM client
        s3_client: boto3 S3 client
        jenkins_role: Jenkins role name
        tomcat_role: Tomcat role name
        codedeploy_instance_role: CodeDeploy instance role name
        codedeploy_service_role: CodeDeploy service role name
        codebuild_service_role: CodeBuild service role name
        s3_bucket: S3 bucket name
        region: AWS region
        
    Returns:
        bool: True if all resources exist, False otherwise
    """
    def print_log(message, level="INFO"):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {message}", flush=True)
    
    all_valid = True
    
    # Check S3 bucket
    try:
        s3_client.head_bucket(Bucket=s3_bucket)
        print_log(f" S3 bucket exists: {s3_bucket}")
    except ClientError:
        print_log(f"S3 bucket not found: {s3_bucket}", "ERROR")
        all_valid = False
    
    # Check IAM roles and instance profiles
    roles_to_check = [
        jenkins_role,
        codedeploy_instance_role,
        codedeploy_service_role,
        codebuild_service_role
    ]
    
    # Add tomcat_role only if it's different from codedeploy_instance_role
    if tomcat_role != codedeploy_instance_role:
        roles_to_check.append(tomcat_role)
    
    for role_name in roles_to_check:
        try:
            iam_client.get_role(RoleName=role_name)
            print_log(f" IAM role exists: {role_name}")
        except ClientError:
            print_log(f"IAM role not found: {role_name}", "ERROR")
            all_valid = False
    
    # Check instance profiles for EC2 roles
    instance_profiles_to_check = [
        jenkins_role,
        codedeploy_instance_role
    ]
    
    if tomcat_role != codedeploy_instance_role:
        instance_profiles_to_check.append(tomcat_role)
    
    for profile_name in instance_profiles_to_check:
        try:
            iam_client.get_instance_profile(InstanceProfileName=profile_name)
            print_log(f" Instance profile exists: {profile_name}")
        except ClientError:
            print_log(f"Instance profile not found: {profile_name}", "ERROR")
            all_valid = False
    
    if all_valid:
        print_log(" All IAM resources validated successfully")
    else:
        print_log(" Some IAM resources are missing", "ERROR")
    
    return all_valid