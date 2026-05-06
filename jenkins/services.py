#!/usr/bin/env python3
"""
Jenkins Infrastructure Service Functions
Functions for creating and managing Jenkins infrastructure resources
"""

import os
import re
import json
import base64
import time
import urllib.request
import urllib.error
from pathlib import Path
from botocore.exceptions import ClientError


def print_log(message, level="INFO"):
    """Print log message to stdout"""
    timestamp = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def parse_sg_rules(rules_string):
    """
    Parse security group rules from config string
    Format: port,protocol,source|port,protocol,source
    """
    rules = []
    for rule_str in rules_string.split('|'):
        parts = rule_str.split(',')
        if len(parts) == 3:
            rules.append({
                'port': int(parts[0]),
                'protocol': parts[1],
                'source': parts[2]
            })
    return rules


def create_security_groups(ec2_client, vpc_id, jenkins_sg_name, jenkins_sg_desc, jenkins_rules_str,
                          app_sg_name, app_sg_desc, app_rules_str):
    """
    Create security groups in VPC (idempotent)
    Week 1: Creates Jenkins and Application server security groups
    Week 2+: Creates only Jenkins security group (app_sg parameters will be None)
    
    Returns:
        dict: Dictionary mapping security group names to IDs
    """
    print_log(f"Creating security groups in VPC: {vpc_id}")
    
    # Build security groups config - only include app SG if parameters are provided
    security_groups_config = {
        jenkins_sg_name: {
            'description': jenkins_sg_desc,
            'rules': parse_sg_rules(jenkins_rules_str)
        }
    }
    
    # Only add application server SG if parameters are provided (Week 1 mode)
    if app_sg_name and app_sg_desc and app_rules_str:
        security_groups_config[app_sg_name] = {
            'description': app_sg_desc,
            'rules': parse_sg_rules(app_rules_str)
        }
    
    created_sgs = {}
    for sg_name, sg_config in security_groups_config.items():
        try:
            # Check if security group exists
            response = ec2_client.describe_security_groups(
                Filters=[
                    {'Name': 'group-name', 'Values': [sg_name]},
                    {'Name': 'vpc-id', 'Values': [vpc_id]}
                ]
            )
            if response['SecurityGroups']:
                sg_id = response['SecurityGroups'][0]['GroupId']
                print_log(f" Security group {sg_name} already exists: {sg_id}")
                created_sgs[sg_name] = sg_id
            else:
                # Create security group in custom VPC
                sg_response = ec2_client.create_security_group(
                    GroupName=sg_name,
                    Description=sg_config['description'],
                    VpcId=vpc_id
                )
                sg_id = sg_response['GroupId']
                print_log(f" Created security group {sg_name}: {sg_id}")
                created_sgs[sg_name] = sg_id
                
                # Add rules
                for rule in sg_config['rules']:
                    ec2_client.authorize_security_group_ingress(
                        GroupId=sg_id,
                        IpPermissions=[
                            {
                                'IpProtocol': rule['protocol'],
                                'FromPort': rule['port'],
                                'ToPort': rule['port'],
                                'IpRanges': [{'CidrIp': rule['source']}]
                            }
                        ]
                    )
                print_log(f"   Added rules to security group {sg_name}")
                
        except ClientError as e:
            print_log(f"Error with security group {sg_name}: {e}", "ERROR")
    
    return created_sgs


def substitute_userdata_variables(userdata_content, script_type, config_values):
    """
    Substitute placeholder variables in userdata with config values
    Placeholders use {{VARIABLE_NAME}} syntax
    
    Args:
        userdata_content: The userdata content with placeholders
        script_type: Type of script (jenkins, tomcat, tomcat_codedeploy)
        config_values: Dictionary of configuration values
        
    Returns:
        str: Userdata content with substituted values
    """
    result = userdata_content
    substituted_vars = []
    
    # Perform substitution
    for key, value in config_values.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in result:
            result = result.replace(placeholder, str(value))
            substituted_vars.append(key)
    
    # Log substituted variables (but not sensitive values)
    sensitive_vars = {'JENKINS_PASSWORD', 'AWS_ACCOUNT_ID'}
    safe_vars = [var for var in substituted_vars if var not in sensitive_vars]
    if safe_vars:
        print_log(f"Substituted variables in {script_type} userdata: {', '.join(safe_vars)}")
    if any(var in substituted_vars for var in sensitive_vars):
        print_log(f"Substituted {len([v for v in substituted_vars if v in sensitive_vars])} sensitive variable(s)")
    
    # Check for any remaining unsubstituted variables
    remaining = re.findall(r'\{\{([A-Z_]+)\}\}', result)
    if remaining:
        print_log(f"ERROR: Unsubstituted variables in {script_type} userdata: {remaining}", "ERROR")
        raise ValueError(f"Unsubstituted variables found: {remaining}")
    
    return result


def get_userdata(userdata_file_path, script_type, config_values):
    """
    Get user data script based on type with variable substitution
    
    Args:
        userdata_file_path: Path to the userdata.sh file
        script_type: Type of script (jenkins, tomcat, tomcat_codedeploy)
        config_values: Dictionary of configuration values for substitution
        
    Returns:
        str: User data script content with substituted variables
    """
    try:
        with open(userdata_file_path, 'r') as file:
            content = file.read()
        
        # Define markers based on script type
        markers = {
            'jenkins': {
                'start': "# ===== JENKINS_USERDATA_START =====",
                'end': "# ===== JENKINS_USERDATA_END =====",
                'name': 'Jenkins'
            },
            'tomcat_codedeploy': {
                'start': "# ===== TOMCAT_CODEDEPLOY_USERDATA_START =====",
                'end': "# ===== TOMCAT_CODEDEPLOY_USERDATA_END =====",
                'name': 'Tomcat CodeDeploy'
            }
        }
        
        if script_type not in markers:
            raise ValueError(f"Invalid script_type: {script_type}")
        
        start_marker = markers[script_type]['start']
        end_marker = markers[script_type]['end']
        script_name = markers[script_type]['name']
        
        # Extract section between markers
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx == -1 or end_idx == -1:
            raise ValueError(f"{script_name} userdata markers not found in {userdata_file_path}")
        
        userdata_content = content[start_idx + len(start_marker):end_idx].strip()
        
        # Substitute variables in the userdata content
        userdata_content = substitute_userdata_variables(userdata_content, script_type, config_values)
        
        # Check size (AWS EC2 UserData limit is 16KB)
        size_bytes = len(userdata_content.encode('utf-8'))
        print_log(f"{script_name} UserData size: {size_bytes} bytes (limit: 16384)")
        
        if size_bytes > 16384:
            print_log(f"{script_name} UserData exceeds 16KB limit!", "WARNING")
        
        return userdata_content
        
    except FileNotFoundError:
        print_log(f"Userdata file not found: {userdata_file_path}", "ERROR")
        raise
    except Exception as e:
        print_log(f"Error reading {script_type} userdata: {str(e)}", "ERROR")
        raise


def get_latest_debian_ami(ec2_client, debian_owner, preferred_version, fallback_version):
    """
    Get latest Debian AMI ID
    
    Returns:
        str: AMI ID or None if not found
    """
    try:
        response = ec2_client.describe_images(
            Owners=[debian_owner],
            Filters=[
                {'Name': 'name', 'Values': [f'debian-{preferred_version}-*']},
                {'Name': 'architecture', 'Values': ['x86_64']},
                {'Name': 'root-device-type', 'Values': ['ebs']},
                {'Name': 'virtualization-type', 'Values': ['hvm']},
                {'Name': 'state', 'Values': ['available']}
            ]
        )
        
        if not response['Images']:
            print_log(f"Debian {preferred_version} not found, trying Debian {fallback_version}", "WARNING")
            response = ec2_client.describe_images(
                Owners=[debian_owner],
                Filters=[
                    {'Name': 'name', 'Values': [f'debian-{fallback_version}-*']},
                    {'Name': 'architecture', 'Values': ['x86_64']},
                    {'Name': 'root-device-type', 'Values': ['ebs']},
                    {'Name': 'virtualization-type', 'Values': ['hvm']},
                    {'Name': 'state', 'Values': ['available']}
                ]
            )
        
        if response['Images']:
            sorted_images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
            ami_id = sorted_images[0]['ImageId']
            print_log(f"Using Debian AMI: {ami_id}")
            return ami_id
        else:
            print_log("No suitable Debian AMI found", "ERROR")
            return None
            
    except ClientError as e:
        print_log(f"Error finding Debian AMI: {e}", "ERROR")
        return None


def create_jenkins_instance(ec2_client, ami_id, instance_type, key_pair_name, jenkins_sg_id,
                           public_subnet_id, userdata, jenkins_role, instance_name,
                           project_name, environment):
    """
    Create Jenkins EC2 instance in public subnet (idempotent)
    
    Returns:
        tuple: (instance_id, is_new_instance)
    """
    try:
        # Check if Jenkins instance already exists
        response = ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Name', 'Values': [instance_name]},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )
        
        if response['Reservations']:
            instance = response['Reservations'][0]['Instances'][0]
            instance_id = instance['InstanceId']
            public_ip = instance.get('PublicIpAddress', 'Not assigned yet')
            instance_state = instance['State']['Name']
            
            print_log(f" Jenkins instance already exists: {instance_id} (State: {instance_state})")
            
            # Wait for running state if pending
            if instance_state == 'pending':
                print_log("Waiting for instance to reach running state...")
                waiter = ec2_client.get_waiter('instance_running')
                waiter.wait(InstanceIds=[instance_id])
                
                # Get updated public IP
                response = ec2_client.describe_instances(InstanceIds=[instance_id])
                public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress', 'Not assigned')
            
            print_log(f"Jenkins instance public IP: {public_ip}")
            if public_ip != 'Not assigned yet':
                print_log(f"Jenkins URL: http://{public_ip}:8080")
            
            return instance_id, False
        
        # No existing instance found - create new one
        print_log(f"Creating new Jenkins instance in subnet: {public_subnet_id}...")
        response = ec2_client.run_instances(
            ImageId=ami_id,
            MinCount=1,
            MaxCount=1,
            InstanceType=instance_type,
            KeyName=key_pair_name,
            SecurityGroupIds=[jenkins_sg_id],
            SubnetId=public_subnet_id,
            UserData=userdata,
            IamInstanceProfile={'Name': jenkins_role},
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'Name', 'Value': instance_name},
                        {'Key': 'Project', 'Value': project_name},
                        {'Key': 'Environment', 'Value': environment}
                    ]
                }
            ]
        )
        
        instance_id = response['Instances'][0]['InstanceId']
        print_log(f" Created Jenkins instance: {instance_id}")
        
        # Wait for instance to be running and get public IP
        print_log("Waiting for Jenkins instance to be running...")
        waiter = ec2_client.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id])
        
        # Get public IP
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress', 'Not assigned')
        print_log(f"Jenkins instance public IP: {public_ip}")
        print_log(f"Jenkins URL: http://{public_ip}:8080")
        print_log("Access Jenkins using the provided credentials.")
        
        return instance_id, True
        
    except ClientError as e:
        print_log(f"Error managing Jenkins instance: {e}", "ERROR")
        return None, False


def create_tomcat_server(ec2_client, ami_id, instance_type, key_pair_name, app_sg_id,
                        public_subnet_id, userdata, codedeploy_role, instance_name,
                        project_name, environment):
    """
    Create sample Tomcat server in public subnet (idempotent)
    
    Returns:
        bool: True if successful, False otherwise
    """
    print_log("Creating sample Tomcat server for CodeDeploy...")
    
    try:
        # Check if tomcat server already exists
        response = ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Name', 'Values': [instance_name]},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )
        
        if response['Reservations']:
            instance = response['Reservations'][0]['Instances'][0]
            instance_id = instance['InstanceId']
            public_ip = instance.get('PublicIpAddress', 'Not assigned yet')
            instance_state = instance['State']['Name']
            
            print_log(f" Tomcat server already exists: {instance_id} (State: {instance_state})")
            
            # Wait for running state if pending
            if instance_state == 'pending':
                print_log("Waiting for instance to reach running state...")
                waiter = ec2_client.get_waiter('instance_running')
                waiter.wait(InstanceIds=[instance_id])
                
                # Get updated public IP
                response = ec2_client.describe_instances(InstanceIds=[instance_id])
                public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress', 'Not assigned')
            
            print_log(f"Tomcat server public IP: {public_ip}")
            if public_ip != 'Not assigned yet':
                print_log(f"Tomcat URL: http://{public_ip}:8080")
            
            return True
        
        # No existing instance found - create new one
        print_log(f"Creating new Tomcat server instance in subnet: {public_subnet_id}...")
        response = ec2_client.run_instances(
            ImageId=ami_id,
            MinCount=1,
            MaxCount=1,
            InstanceType=instance_type,
            KeyName=key_pair_name,
            SecurityGroupIds=[app_sg_id],
            SubnetId=public_subnet_id,
            UserData=userdata,
            IamInstanceProfile={'Name': codedeploy_role},
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'Name', 'Value': instance_name},
                        {'Key': 'Project', 'Value': project_name},
                        {'Key': 'Environment', 'Value': environment}
                    ]
                }
            ]
        )
        
        instance_id = response['Instances'][0]['InstanceId']
        print_log(f" Created sample Tomcat server: {instance_id}")
        
        # Wait for instance to be running
        print_log("Waiting for Tomcat server to be running...")
        waiter = ec2_client.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id])
        
        # Get public IP
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress', 'Not assigned')
        print_log(f"Tomcat server public IP: {public_ip}")
        
        if public_ip != 'Not assigned':
            print_log(f"Tomcat URL: http://{public_ip}:8080")
            print_log("Note: Tomcat installation may take 3-5 minutes to complete")
        
        return True
        
    except ClientError as e:
        print_log(f"Error managing Tomcat server: {e}", "ERROR")
        return False


def create_codebuild_project(codebuild_client, project_name, github_repo, buildspec_file,
                            build_image, compute_type, s3_bucket, service_role_arn,
                            connection_arn, region, account_id, timeout_minutes,
                            queued_timeout_minutes, project_tag, env_tag):
    """
    Create CodeBuild project with CodeStar connection for GitHub (idempotent)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Check if project exists
        response = codebuild_client.batch_get_projects(names=[project_name])
        
        if response['projects']:
            existing_project = response['projects'][0]
            print_log(f"CodeBuild project {project_name} already exists")
            
            # Verify project configuration
            needs_update = False
            updates_needed = []
            
            # Check source configuration
            current_source = existing_project.get('source', {})
            if current_source.get('location') != github_repo:
                needs_update = True
                updates_needed.append("source location")
            
            current_auth = current_source.get('auth', {})
            if current_auth.get('resource') != connection_arn:
                needs_update = True
                updates_needed.append("CodeStar connection")
            
            # Check artifacts configuration
            current_artifacts = existing_project.get('artifacts', {})
            if current_artifacts.get('location') != s3_bucket:
                needs_update = True
                updates_needed.append("S3 bucket")
            
            # Check service role
            current_role = existing_project.get('serviceRole', '')
            if current_role != service_role_arn:
                needs_update = True
                updates_needed.append("service role")
            
            if needs_update:
                print_log(f"Project exists but needs updates: {', '.join(updates_needed)}", "WARNING")
                print_log("Updating CodeBuild project configuration...")
                
                try:
                    codebuild_client.update_project(
                        name=project_name,
                        description='Hello World application build project',
                        source={
                            'type': 'GITHUB',
                            'location': github_repo,
                            'gitCloneDepth': 1,
                            'buildspec': buildspec_file,
                            'auth': {
                                'type': 'CODECONNECTIONS',
                                'resource': connection_arn
                            },
                            'reportBuildStatus': False,
                            'insecureSsl': False
                        },
                        artifacts={
                            'type': 'S3',
                            'location': s3_bucket,
                            'name': 'codebuild-artifact.zip',
                            'packaging': 'ZIP',
                            'overrideArtifactName': False
                        },
                        environment={
                            'type': 'LINUX_CONTAINER',
                            'image': build_image,
                            'computeType': compute_type,
                            'environmentVariables': [
                                {
                                    'name': 'AWS_DEFAULT_REGION',
                                    'value': region,
                                    'type': 'PLAINTEXT'
                                },
                                {
                                    'name': 'AWS_ACCOUNT_ID',
                                    'value': account_id,
                                    'type': 'PLAINTEXT'
                                }
                            ]
                        },
                        serviceRole=service_role_arn
                    )
                    print_log(f"Updated CodeBuild project configuration")
                except ClientError as update_error:
                    print_log(f"Failed to update project: {update_error}", "ERROR")
                    return False
            else:
                print_log("Project configuration is correct")
            
            return True
        
        # No existing project - create new one
        print_log(f"Creating CodeBuild project: {project_name}")
        
        create_response = codebuild_client.create_project(
            name=project_name,
            description='Hello World application build project',
            source={
                'type': 'GITHUB',
                'location': github_repo,
                'gitCloneDepth': 1,
                'buildspec': buildspec_file,
                'auth': {
                    'type': 'CODECONNECTIONS',
                    'resource': connection_arn
                },
                'reportBuildStatus': False,
                'insecureSsl': False
            },
            artifacts={
                'type': 'S3',
                'location': s3_bucket,
                'name': 'codebuild-artifact.zip',
                'packaging': 'ZIP',
                'overrideArtifactName': False
            },
            environment={
                'type': 'LINUX_CONTAINER',
                'image': build_image,
                'computeType': compute_type,
                'environmentVariables': [
                    {
                        'name': 'AWS_DEFAULT_REGION',
                        'value': region,
                        'type': 'PLAINTEXT'
                    },
                    {
                        'name': 'AWS_ACCOUNT_ID',
                        'value': account_id,
                        'type': 'PLAINTEXT'
                    }
                ]
            },
            serviceRole=service_role_arn,
            timeoutInMinutes=timeout_minutes,
            queuedTimeoutInMinutes=queued_timeout_minutes,
            tags=[
                {'key': 'Project', 'value': project_tag},
                {'key': 'Environment', 'value': env_tag}
            ]
        )
        
        print_log(f"Created CodeBuild project: {project_name}")
        print_log(f"Project ARN: {create_response['project']['arn']}")
        return True
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        
        if error_code == 'ResourceAlreadyExistsException':
            print_log(f"CodeBuild project {project_name} already exists")
            return True
        elif error_code == 'InvalidInputException' and 'connection' in str(e).lower():
            print_log("CodeStar connection is not available or not authorized", "ERROR")
            return False
        else:
            print_log(f"Error creating CodeBuild project: {e}", "ERROR")
            return False


def create_codedeploy_application(codedeploy_client, app_name, deployment_group_name,
                                 service_role_arn, tomcat_instance_name):
    """
    Create CodeDeploy application and deployment group (idempotent)
    
    Returns:
        bool: True if successful, False otherwise
    """
    print_log("Ensuring CodeDeploy application and deployment group exist...")
    
    # Desired configuration
    desired_ec2_tags = [
        {
            'Type': 'KEY_AND_VALUE',
            'Key': 'Name',
            'Value': tomcat_instance_name
        }
    ]
    
    # 1. Ensure CodeDeploy application exists
    try:
        codedeploy_client.get_application(applicationName=app_name)
        print_log(f"CodeDeploy application {app_name} already exists")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ApplicationDoesNotExistException':
            try:
                codedeploy_client.create_application(
                    applicationName=app_name,
                    computePlatform='Server'
                )
                print_log(f"Created CodeDeploy application: {app_name}")
            except ClientError as create_error:
                print_log(f"Failed to create CodeDeploy application: {create_error}", "ERROR")
                return False
        else:
            print_log(f"Error checking CodeDeploy application: {e}", "ERROR")
            return False
    
    # 2. Ensure deployment group exists and is correctly configured
    try:
        response = codedeploy_client.get_deployment_group(
            applicationName=app_name,
            deploymentGroupName=deployment_group_name
        )
        
        # Check if configuration is correct
        deployment_group = response['deploymentGroupInfo']
        needs_update = False
        update_params = {
            'applicationName': app_name,
            'currentDeploymentGroupName': deployment_group_name
        }
        
        # Check service role
        if deployment_group.get('serviceRoleArn') != service_role_arn:
            needs_update = True
            update_params['serviceRoleArn'] = service_role_arn
            print_log(f"Service role mismatch - will update")
        
        # Check EC2 tag filters
        existing_tags = deployment_group.get('ec2TagFilters', [])
        if existing_tags != desired_ec2_tags:
            needs_update = True
            update_params['ec2TagFilters'] = desired_ec2_tags
            print_log(f"EC2 tag filters mismatch - will update")
        
        if needs_update:
            try:
                codedeploy_client.update_deployment_group(**update_params)
                print_log(f"Updated CodeDeploy deployment group: {deployment_group_name}")
            except ClientError as update_error:
                print_log(f"Failed to update deployment group: {update_error}", "ERROR")
                return False
        else:
            print_log(f"CodeDeploy deployment group {deployment_group_name} is correctly configured")
            
    except ClientError as e:
        if e.response['Error']['Code'] == 'DeploymentGroupDoesNotExistException':
            try:
                codedeploy_client.create_deployment_group(
                    applicationName=app_name,
                    deploymentGroupName=deployment_group_name,
                    serviceRoleArn=service_role_arn,
                    ec2TagFilters=desired_ec2_tags
                )
                print_log(f"Created CodeDeploy deployment group: {deployment_group_name}")
            except ClientError as create_error:
                print_log(f"Failed to create CodeDeploy deployment group: {create_error}", "ERROR")
                return False
        else:
            print_log(f"Error checking CodeDeploy deployment group: {e}", "ERROR")
            return False
    
    return True


def wait_for_jenkins(ec2_client, instance_id, jenkins_initial_startup, jenkins_plugin_installation,
                    jenkins_verification_attempts, jenkins_final_verification_attempts, wait_interval):
    """
    Wait for Jenkins to be fully operational with proper timing
    
    Returns:
        str: Jenkins URL or None if failed
    """
    print_log("Waiting for Jenkins to be fully operational...")
    
    # Get instance public IP
    max_attempts = 30
    public_ip = None
    
    for attempt in range(max_attempts):
        try:
            response = ec2_client.describe_instances(InstanceIds=[instance_id])
            public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress')
            if public_ip:
                print_log(f"Jenkins instance public IP: {public_ip}")
                break
            time.sleep(wait_interval)
        except Exception as e:
            print_log(f"Error getting public IP: {e}", "WARNING")
            time.sleep(wait_interval)
    
    if not public_ip:
        print_log("Could not get Jenkins public IP", "ERROR")
        return None
    
    jenkins_url = f"http://{public_ip}:8080"
    print_log(f"Jenkins URL will be: {jenkins_url}")
    
    # Wait for userdata script completion first
    print_log(f"Phase 1: Waiting for initial Jenkins startup ({jenkins_initial_startup // 60} minutes)...")
    time.sleep(jenkins_initial_startup)
    
    # Check for Jenkins initial response
    print_log("Phase 2: Checking for Jenkins initial response...")
    initial_ready = False
    
    for attempt in range(jenkins_verification_attempts):
        try:
            request = urllib.request.Request(
                f"{jenkins_url}/login",
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            response = urllib.request.urlopen(request, timeout=10)
            if response.getcode() == 200:
                print_log("Jenkins is responding!")
                initial_ready = True
                break
        except Exception:
            pass
        
        if attempt % 6 == 0:
            print_log(f"Attempt {attempt + 1}/{jenkins_verification_attempts}: Waiting for Jenkins to respond...")
        time.sleep(wait_interval)
    
    if not initial_ready:
        print_log(f"Jenkins not responding after {jenkins_verification_attempts * wait_interval // 60} minutes, but continuing...", "WARNING")
    
    # Wait for plugin installation and potential restart
    print_log(f"Phase 3: Waiting for plugin installation and restart ({jenkins_plugin_installation // 60} minutes)...")
    time.sleep(jenkins_plugin_installation)
    
    # Final verification that Jenkins is ready
    print_log("Phase 4: Final verification...")
    
    for attempt in range(jenkins_final_verification_attempts):
        try:
            # Try to access both login page and check if setup is complete
            request = urllib.request.Request(
                f"{jenkins_url}/login",
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            response = urllib.request.urlopen(request, timeout=10)
            response_text = response.read().decode('utf-8')
            
            # Check if we're not in setup wizard
            if response.getcode() == 200 and 'Getting Started' not in response_text:
                print_log("Jenkins setup appears complete!")
                
                # Also try to access the main page
                main_request = urllib.request.Request(
                    jenkins_url,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                main_response = urllib.request.urlopen(main_request, timeout=10)
                if main_response.getcode() == 200:
                    print_log(f"Jenkins is fully ready at {jenkins_url}")
                    return jenkins_url
                    
        except Exception as e:
            if attempt % 6 == 0:
                print_log(f"Final verification attempt {attempt + 1}/{jenkins_final_verification_attempts}...")
        
        time.sleep(wait_interval)
    
    print_log("Jenkins setup may still be in progress. Please check manually.", "WARNING")
    print_log(f"Jenkins URL: {jenkins_url}")
    
    return jenkins_url


def create_key_pair(ec2_client, key_pair_name, project_name, managed_by, save_locally=True):
    """
    Create EC2 key pair if it doesn't exist (idempotent)
    
    Returns:
        bool: True if successful, False otherwise
    """
    print_log(f"Ensuring key pair '{key_pair_name}' exists...")

    try:
        # Check if key pair already exists
        response = ec2_client.describe_key_pairs(KeyNames=[key_pair_name])

        if response.get('KeyPairs'):
            print_log(f" Key pair '{key_pair_name}' already exists")
            # If the key exists in AWS but private key is not present locally, inform the user
            try:
                from pathlib import Path
                local_path = Path.home() / 'Downloads' / f"{key_pair_name}.pem"
                if local_path.exists():
                    print_log(f" Private key file exists locally at: {local_path}")
                else:
                    print_log(
                        f"⚠ Private key file not found at {local_path}.\n"
                        " To obtain a new private key you must delete the existing key pair in AWS and re-run the script to create/download a new key.\n"
                        "  - If you delete the key pair, existing EC2 instances launched with the old key will NOT automatically accept the new key.\n"
                        "  - To regain access to an existing instance you can use AWS Session Manager (SSM), or modify the root volume via a helper instance.\n"
                        "See documentation or ask for help if you need step-by-step guidance.",
                        "WARNING"
                    )
            except Exception:
                pass
            return True

    except ClientError as e:
        # If key pair not found, create it and optionally save the private key
        if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
            try:
                response = ec2_client.create_key_pair(
                    KeyName=key_pair_name,
                    KeyType='rsa',
                    TagSpecifications=[
                        {
                            'ResourceType': 'key-pair',
                            'Tags': [
                                {'Key': 'Name', 'Value': key_pair_name},
                                {'Key': 'Project', 'Value': project_name},
                                {'Key': 'ManagedBy', 'Value': managed_by}
                            ]
                        }
                    ]
                )

                print_log(f" Created key pair: {key_pair_name}")

                # Optionally save private key locally to the user's Downloads folder
                if save_locally:
                    try:
                        from pathlib import Path
                        import os

                        downloads_dir = str(Path.home() / 'Downloads')
                        # Create Downloads directory if it doesn't exist
                        os.makedirs(downloads_dir, exist_ok=True)

                        key_file_path = os.path.join(downloads_dir, f"{key_pair_name}.pem")
                        with open(key_file_path, 'w') as key_file:
                            key_file.write(response['KeyMaterial'])

                        # Set appropriate permissions (Unix-like systems)
                        os.chmod(key_file_path, 0o400)
                        print_log(f" Saved private key to: {key_file_path}")
                    except Exception as file_error:
                        print_log(f"⚠ Created key pair but couldn't save to Downloads: {file_error}", "WARNING")
                else:
                    print_log(f"⚠ Key pair created but private key not saved locally (save_locally=False)", "WARNING")

                return True

            except ClientError as create_error:
                print_log(f"Failed to create key pair: {create_error}", "ERROR")
                return False
        else:
            print_log(f"Error checking key pair: {e}", "ERROR")
            return False

    except Exception as e:
        print_log(f"Unexpected error with key pair: {e}", "ERROR")
        return False