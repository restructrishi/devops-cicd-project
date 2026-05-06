#!/usr/bin/env python3
"""
Jenkins Infrastructure Automation Script
Functional approach for creating Jenkins infrastructure resources.
Called by main.sh with CLI arguments.
"""

import sys
import os
import argparse
import time
from pathlib import Path

# Add parent directory to path to import readconfig
sys.path.append(str(Path(__file__).parent.parent))
from readconfig import get_config_value

# Import modules
from aws_client import initialize_aws_clients, print_log
from validators import validate_network_infrastructure, verify_codestar_connection, validate_iam_resources
from services import (
    create_security_groups,
    get_userdata,
    get_latest_debian_ami,
    create_jenkins_instance,
    create_tomcat_server,
    create_codebuild_project,
    create_codedeploy_application,
    wait_for_jenkins,
    create_key_pair
)


def get_userdata_config_values(region, account_id, s3_bucket, jenkins_user, jenkins_password,
                               codedeploy_app_name, codedeploy_deployment_group):
    """
    Build configuration values dictionary for userdata substitution
    
    Returns:
        dict: Configuration values for userdata variable substitution
    """
    return {
        'JENKINS_USER': jenkins_user,
        'JENKINS_PASSWORD': jenkins_password,
        'S3_BUCKET': s3_bucket,
        'AWS_REGION': region,
        'AWS_ACCOUNT_ID': account_id,
        'CODEDEPLOY_APP_NAME': codedeploy_app_name,
        'CODEDEPLOY_DEPLOYMENT_GROUP': codedeploy_deployment_group,
        'ARTIFACT_KEY': 'codebuild-artifact.zip',
        'WEBAPPS_DIR': '/var/lib/tomcat10/webapps',
        'TOMCAT_USER': 'tomcat',
        'TOMCAT_GROUP': 'tomcat',
    }


def run_jenkins_automation(region, account_id, code_deploy_infra, config_file='config.ini'):
    """
    Main orchestration function for Jenkins infrastructure automation
    Called from main.sh with CLI arguments
    
    Args:
        region: AWS region
        account_id: AWS account ID
        code_deploy_infra: 'true' to create CodeDeploy infrastructure (Week 1), 'false' to skip (Week 2+)
        config_file: Path to configuration file
        
    Returns:
        bool: True if successful, False otherwise
    """
    print_log("=" * 60)
    print_log("Jenkins Infrastructure Automation Starting")
    print_log(f"Mode: {'Week 1 - EC2 + CodeDeploy' if code_deploy_infra == 'true' else 'Week 2+ - CodeBuild only'}")
    print_log("=" * 60)
    print_log("=" * 60)
    
    try:
        # Initialize AWS clients
        clients = initialize_aws_clients(region)
        
        # Step 0: Validate IAM resources exist (created by iam_service.py)
        print_log("Step 0: Validating IAM resources...")
        jenkins_role = get_config_value('IAM', 'jenkins_role')
        tomcat_role = get_config_value('IAM', 'tomcat_role')
        codedeploy_instance_role = get_config_value('IAM', 'codedeploy_instance_role')
        codedeploy_service_role = get_config_value('IAM', 'codedeploy_service_role')
        codebuild_service_role = get_config_value('IAM', 'codebuild_service_role')
        s3_bucket = get_config_value('S3', 'bucket_name')
        
        if not validate_iam_resources(clients['iam'], clients['s3'], 
                                     jenkins_role, tomcat_role,
                                     codedeploy_instance_role, codedeploy_service_role,
                                     codebuild_service_role, s3_bucket, region):
            return False
        
        # Get all configuration values
        vpc_name = get_config_value('vpc', 'name')
        vpc_cidr = get_config_value('vpc', 'CIDR')
        public_subnet_name = get_config_value('subnet', 'subnet1_name')
        public_subnet_cidr = get_config_value('subnet', 'subnet1_cidr')
        igw_name = get_config_value('igw', 'name')
        rt_name = get_config_value('rt', 'rt1_name')
        
        s3_bucket = get_config_value('S3', 'bucket_name')
        key_pair_name = get_config_value('EC2', 'key_pair_name')
        
        jenkins_instance_name = get_config_value('EC2', 'jenkins_instance_name')
        jenkins_instance_type = get_config_value('EC2', 'jenkins_instance_type')
        jenkins_role = get_config_value('IAM', 'jenkins_role')
        jenkins_user = get_config_value('Jenkins', 'jenkins_user')
        jenkins_password = get_config_value('Jenkins', 'jenkins_password')
        jenkins_userdata_script = get_config_value('Jenkins', 'userdata_script')
        jenkins_userdata_type = get_config_value('Jenkins', 'userdata_type')
        
        # Week 1 specific configuration (only load if code_deploy_infra is true)
        if code_deploy_infra == 'true':
            tomcat_instance_name = get_config_value('EC2', 'tomcat_instance_name')
            tomcat_instance_type = get_config_value('EC2', 'tomcat_instance_type')
            tomcat_codedeploy_userdata_type = get_config_value('Tomcat', 'codedeploy_userdata_type')
            codedeploy_instance_role = get_config_value('IAM', 'codedeploy_instance_role')
            
            app_sg_name = get_config_value('SecurityGroups', 'app_server_sg_name')
            app_sg_desc = get_config_value('SecurityGroups', 'app_server_sg_description')
            app_rules_str = get_config_value('SecurityGroupRules', 'app_server_rules')
            
            codedeploy_app_name = get_config_value('CodeDeploy', 'app_name')
            codedeploy_deployment_group = get_config_value('CodeDeploy', 'deployment_group_name')
            codedeploy_service_role = get_config_value('IAM', 'codedeploy_service_role')
        
        jenkins_sg_name = get_config_value('SecurityGroups', 'jenkins_sg_name')
        jenkins_sg_desc = get_config_value('SecurityGroups', 'jenkins_sg_description')
        jenkins_rules_str = get_config_value('SecurityGroupRules', 'jenkins_rules')
        
        debian_owner = get_config_value('AMI', 'debian_owner_id')
        preferred_debian = get_config_value('AMI', 'preferred_debian_version')
        fallback_debian = get_config_value('AMI', 'fallback_debian_version')
        
        codebuild_project_name = get_config_value('CodeBuild', 'project_name')
        github_repo = get_config_value('CodeBuild', 'github_repo')
        buildspec_file = get_config_value('CodeBuild', 'buildspec_file')
        build_image = get_config_value('CodeBuild', 'build_image')
        compute_type = get_config_value('CodeBuild', 'compute_type')
        build_timeout = int(get_config_value('CodeBuild', 'build_timeout'))
        queued_timeout = int(get_config_value('CodeBuild', 'queued_timeout'))
        codebuild_service_role = get_config_value('IAM', 'codebuild_service_role')
        
        project_name = get_config_value('Tags', 'project_name')
        env_cicd = get_config_value('Tags', 'environment_cicd')
        env_dev = get_config_value('Tags', 'environment_dev')
        env_prod = get_config_value('Tags', 'environment_prod')
        managed_by = get_config_value('Tags', 'managed_by')
        
        connection_name = get_config_value('CodeConnections', 'connection_name')
        connection_arn = get_config_value('CodeConnections', 'connection_arn')
        
        iam_propagation_wait = int(get_config_value('Timeouts', 'iam_propagation_wait'))
        jenkins_initial_startup = int(get_config_value('Timeouts', 'jenkins_initial_startup'))
        jenkins_plugin_installation = int(get_config_value('Timeouts', 'jenkins_plugin_installation'))
        jenkins_verification_attempts = int(get_config_value('Timeouts', 'jenkins_verification_attempts'))
        jenkins_final_verification_attempts = int(get_config_value('Timeouts', 'jenkins_final_verification_attempts'))
        wait_interval = int(get_config_value('Timeouts', 'wait_interval'))
        
        # Step 1: Validate network infrastructure
        print_log("Step 1: Validating network infrastructure...")
        vpc_id, public_subnet_id, igw_id, public_rt_id = validate_network_infrastructure(
            clients['ec2'], vpc_name, vpc_cidr, public_subnet_name,
            public_subnet_cidr, igw_name, rt_name
        )
        
        # Step 2: Create key pair
        print_log("Step 2: Creating key pair...")
        if not create_key_pair(clients['ec2'], key_pair_name, project_name, managed_by, save_locally=True):
            print_log("Failed to create key pair", "ERROR")
            return False
        
        # Step 3: IAM propagation already handled (network setup took 3-4 minutes)
        print_log("Step 3: Skipping IAM propagation wait (already waited during network setup)")
        
        # Step 4: Create security groups
        print_log("Step 4: Creating security groups...")
        if code_deploy_infra == 'true':
            # Week 1: Create both Jenkins and Application server security groups
            security_groups = create_security_groups(
                clients['ec2'], vpc_id, jenkins_sg_name, jenkins_sg_desc, jenkins_rules_str,
                app_sg_name, app_sg_desc, app_rules_str
            )
        else:
            # Week 2+: Only create Jenkins security group
            security_groups = create_security_groups(
                clients['ec2'], vpc_id, jenkins_sg_name, jenkins_sg_desc, jenkins_rules_str,
                None, None, None
            )
        
        if not security_groups:
            print_log("Failed to create security groups", "ERROR")
            return False
        
        # Step 5: Get AMI ID
        print_log("Step 5: Getting latest Debian AMI...")
        ami_id = get_latest_debian_ami(clients['ec2'], debian_owner, preferred_debian, fallback_debian)
        if not ami_id:
            print_log("Could not retrieve Debian AMI", "ERROR")
            return False
        
        # Step 6: Prepare userdata
        print_log("Step 6: Preparing userdata scripts...")
        script_dir = Path(__file__).parent
        userdata_file_path = script_dir / jenkins_userdata_script
        
        # Build config values based on mode
        if code_deploy_infra == 'true':
            config_values = get_userdata_config_values(
                region, account_id, s3_bucket, jenkins_user, jenkins_password,
                codedeploy_app_name, codedeploy_deployment_group
            )
            tomcat_codedeploy_userdata = get_userdata(str(userdata_file_path), tomcat_codedeploy_userdata_type, config_values)
        else:
            config_values = get_userdata_config_values(
                region, account_id, s3_bucket, jenkins_user, jenkins_password,
                '', ''  # Empty CodeDeploy values for Week 2+
            )
        
        jenkins_userdata = get_userdata(str(userdata_file_path), jenkins_userdata_type, config_values)
        
        # Step 7: Create Jenkins instance
        print_log("Step 7: Creating Jenkins instance...")
        jenkins_instance_id, is_new_instance = create_jenkins_instance(
            clients['ec2'], ami_id, jenkins_instance_type, key_pair_name,
            security_groups[jenkins_sg_name], public_subnet_id, jenkins_userdata,
            jenkins_role, jenkins_instance_name, project_name, env_cicd
        )
        if not jenkins_instance_id:
            print_log("Failed to create Jenkins instance", "ERROR")
            return False
        
        # Step 8: Verify CodeStar connection
        print_log("Step 8: Verifying CodeStar connection...")
        verified_connection_arn = verify_codestar_connection(clients['codestar'], connection_name, region)
        if not verified_connection_arn:
            print_log("CodeStar connection is not available", "ERROR")
            print_log("=" * 60, "ERROR")
            print_log("MANUAL ACTION REQUIRED: CodeStar Connection Setup", "ERROR")
            print_log("=" * 60, "ERROR")
            print_log(f"Connection Name: {connection_name}", "ERROR")
            print_log("Steps to create/authorize the connection:", "ERROR")
            print_log("1. Go to AWS Console > Developer Tools > CodeConnections", "ERROR")
            print_log("2. Create/authorize the connection with GitHub", "ERROR")
            print_log("3. Wait for status to become AVAILABLE", "ERROR")
            print_log("4. Re-run this script", "ERROR")
            print_log("=" * 60, "ERROR")
            return False
        
        # Step 9: Create CodeBuild project
        print_log("Step 9: Creating CodeBuild project...")
        service_role_arn = f'arn:aws:iam::{account_id}:role/{codebuild_service_role}'
        if not create_codebuild_project(
            clients['codebuild'], codebuild_project_name, github_repo, buildspec_file,
            build_image, compute_type, s3_bucket, service_role_arn,
            verified_connection_arn, region, account_id, build_timeout,
            queued_timeout, project_name, env_cicd
        ):
            print_log("Failed to create CodeBuild project", "ERROR")
            return False
        
        # Step 10: Create sample Tomcat server (only for Week 1)
        if code_deploy_infra == 'true':
            print_log("Step 10: Creating sample Tomcat server...")
            if not create_tomcat_server(
                clients['ec2'], ami_id, tomcat_instance_type, key_pair_name,
                security_groups[app_sg_name], public_subnet_id, tomcat_codedeploy_userdata,
                codedeploy_instance_role, tomcat_instance_name, project_name, env_dev
            ):
                print_log("Failed to create sample Tomcat server", "ERROR")
                return False
        else:
            print_log("Step 10: Skipping Tomcat server creation (Week 2+ mode")
        
        # Step 11: Create CodeDeploy application (only for Week 1)
        if code_deploy_infra == 'true':
            print_log("Step 11: Creating CodeDeploy application...")
            codedeploy_service_role_arn = f'arn:aws:iam::{account_id}:role/{codedeploy_service_role}'
            if not create_codedeploy_application(
                clients['codedeploy'], codedeploy_app_name, codedeploy_deployment_group,
                codedeploy_service_role_arn, tomcat_instance_name
            ):
                print_log("Failed to create CodeDeploy application", "ERROR")
                return False
        else:
            print_log("Step 11: Skipping CodeDeploy application creation (Week 2+ mode)")
        
        # Step 12: Wait for Jenkins to be ready (only for new instances)
        print_log("Step 12: Checking Jenkins status...")
        if is_new_instance:
            print_log("New Jenkins instance created - waiting for setup to complete (20 minutes)...")
            jenkins_url = wait_for_jenkins(
                clients['ec2'], jenkins_instance_id, jenkins_initial_startup,
                jenkins_plugin_installation, jenkins_verification_attempts,
                jenkins_final_verification_attempts, wait_interval
            )
        else:
            print_log("Existing Jenkins instance found - skipping setup wait")
            response = clients['ec2'].describe_instances(InstanceIds=[jenkins_instance_id])
            public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress')
            jenkins_url = f"http://{public_ip}:8080"
            print_log(f"Jenkins is ready at: {jenkins_url}")
        
                # Final summary
        print_log("="  * 60)
        print_log("Jenkins Infrastructure Setup Complete!")
        print_log("=" * 60)
        
        if jenkins_url:
            print_log(f"Jenkins URL: {jenkins_url}")
        
        print_log("\nResources Created:")
        print_log(f"  S3 Bucket: {s3_bucket}")
        
        if code_deploy_infra == 'true':
            print_log(f"  Security Groups: {jenkins_sg_name}, {app_sg_name}")
        else:
            print_log(f"  Security Groups: {jenkins_sg_name}")
        
        print_log(f"  Jenkins Instance: {jenkins_instance_id}")
        print_log(f"  CodeBuild Project: {codebuild_project_name}")
        
        if code_deploy_infra == 'true':
            print_log(f"  CodeDeploy Application: {codedeploy_app_name}")
            print_log(f"  Sample Tomcat Server: {tomcat_instance_name}")

        
        print_log("\nNext Steps:")
        print_log("1. Access Jenkins and configure pipeline")
        print_log("2. Verify CodeBuild project configuration")
        
        if code_deploy_infra == 'true':
            print_log("3. Test CodeDeploy to sample Tomcat server")
            print_log("4. Verify Auto Scaling Group instances")
        else:
            print_log("3. Ready for Week 2+ components (ALB, ASG, ECS, etc.)")
        
        return True
        
    except Exception as e:
        print_log(f"Automation failed: {str(e)}", "ERROR")
        import traceback
        traceback.print_exc()
        return False


def main():
    """
    Main CLI entry point
    Accepts arguments from main.sh
    """
    parser = argparse.ArgumentParser(description='Jenkins Infrastructure Automation - Called from main.sh')
    parser.add_argument('region', help='AWS region')
    parser.add_argument('account_id', help='AWS account ID')
    parser.add_argument('code_deploy_infra', nargs='?', default='false', 
                       help='Create CodeDeploy infrastructure: "true" (Week 1) or "false" (Week 2+)')
    
    args = parser.parse_args()
    
    # Log the mode
    print(f"Running in mode: {args.code_deploy_infra}")
    
    # Run automation with CLI arguments
    success = run_jenkins_automation(args.region, args.account_id, args.code_deploy_infra)
    
    if success:
        print_log("=" * 60)
        print_log(" Jenkins Infrastructure automation completed successfully")
        print_log("=" * 60)
        sys.exit(0)
    else:
        print_log("=" * 60)
        print_log(" Jenkins Infrastructure automation failed", "ERROR")
        print_log("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()