#!/usr/bin/env python3
"""
Jenkins Pipeline Configuration Automation - Functional Approach
Configures Jenkins with credentials, pipeline job, and GitHub webhook.
"""

import boto3
import requests
from requests.auth import HTTPBasicAuth
from botocore.exceptions import ClientError
import json
import time
import sys
from pathlib import Path


def print_log(message, level="INFO"):
    """Print log message to stdout"""
    timestamp = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def load_script_by_marker(jenkins_scripts_file, marker_name):
    """
    Extract a specific script section using markers from the single file
    
    Args:
        jenkins_scripts_file: Path to the jenkins_scripts.groovy file
        marker_name: Marker name (e.g., 'CREDENTIALS', 'DISABLE_CSRF', 'PIPELINE')
        
    Returns:
        str: Script content between markers
    """
    try:
        with open(jenkins_scripts_file, 'r') as f:
            content = f.read()
        
        start_marker = f"// MARKER: {marker_name}_START"
        end_marker = f"// MARKER: {marker_name}_END"
        
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx == -1:
            raise ValueError(f"Start marker '{start_marker}' not found in {jenkins_scripts_file}")
        if end_idx == -1:
            raise ValueError(f"End marker '{end_marker}' not found in {jenkins_scripts_file}")
        
        # Extract content between markers (skip the start marker line)
        start_content = content.find('\n', start_idx) + 1
        script_content = content[start_content:end_idx].strip()
        
        print_log(f"Loaded script section: {marker_name} ({len(script_content)} chars)")
        return script_content
        
    except FileNotFoundError:
        print_log(f"Script file not found: {jenkins_scripts_file}", "ERROR")
        raise
    except Exception as e:
        print_log(f"Error loading script section {marker_name}: {e}", "ERROR")
        raise


def render_template(template, **kwargs):
    """
    Replace placeholders in template with actual values
    
    Args:
        template: String template with {{PLACEHOLDER}} markers
        **kwargs: Key-value pairs for substitution
        
    Returns:
        str: Rendered template
    """
    for key, value in kwargs.items():
        placeholder = f"{{{{{key}}}}}"
        template = template.replace(placeholder, str(value))
    return template


def load_and_render_script(jenkins_scripts_file, script_type, config_values):
    """
    Load and render a Groovy script by type
    
    Args:
        jenkins_scripts_file: Path to groovy scripts file
        script_type: One of 'credentials', 'disable_csrf', or 'pipeline'
        config_values: Dict of all configuration values
        
    Returns:
        str: Rendered script with placeholders replaced
    """
    # Map script types to markers and their template variables
    script_configs = {
        'credentials': {
            'marker': 'CREDENTIALS',
            'variables': {
                'aws_access_key': config_values['aws_access_key'],
                'aws_secret_key': config_values['aws_secret_key'],
                'github_username': config_values['github_username'],
                'github_token': config_values['github_token']
            }
        },
        'disable_csrf': {
            'marker': 'DISABLE_CSRF',
            'variables': {}  # No template variables needed
        },
        'pipeline': {
            'marker': 'PIPELINE',
            'variables': {
                'aws_region': config_values['region'],
                'codebuild_project': config_values['codebuild_project_name'],
                'codedeploy_app': config_values['codedeploy_app_name'],
                'codedeploy_group': config_values['codedeploy_deployment_group'],
                's3_bucket': config_values['s3_bucket'],
                'github_repo': config_values['github_repo'],
                'github_branch': config_values['github_branch'],
                'application_url': config_values['application_url'],
                'code_deploy_infra': config_values.get('code_deploy_infra', 'true')
            }
        }
    }
    
    if script_type not in script_configs:
        raise ValueError(f"Invalid script_type: {script_type}. Must be one of {list(script_configs.keys())}")
    
    config = script_configs[script_type]
    template = load_script_by_marker(jenkins_scripts_file, config['marker'])
    
    # If there are variables, render the template; otherwise return as-is
    if config['variables']:
        return render_template(template, **config['variables'])
    else:
        return template


def find_jenkins_instance(ec2_client):
    """
    Find Jenkins instance by scanning running instances
    
    Args:
        ec2_client: boto3 EC2 client
        
    Returns:
        tuple: (jenkins_url, instance_id) or (None, None) if not found
    """
    try:
        response = ec2_client.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
        )
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                public_ip = instance.get('PublicIpAddress')
                instance_id = instance['InstanceId']
                
                if public_ip:
                    try:
                        test_url = f"http://{public_ip}:8080/login"
                        test_response = requests.get(test_url, timeout=5)
                        if test_response.status_code == 200 and 'jenkins' in test_response.text.lower():
                            jenkins_url = f"http://{public_ip}:8080"
                            print_log(f"Found Jenkins at {jenkins_url} (instance: {instance_id})")
                            return jenkins_url, instance_id
                    except:
                        continue
        
        print_log("No Jenkins instance found", "ERROR")
        return None, None
        
    except Exception as e:
        print_log(f"Error finding Jenkins: {e}", "ERROR")
        return None, None


def get_crumb(session, jenkins_url):
    """
    Get CSRF crumb token for secure requests
    
    Args:
        session: requests.Session with auth configured
        jenkins_url: Jenkins base URL
        
    Returns:
        tuple: (crumb_field, crumb_value) or (None, None) if unavailable
    """
    try:
        crumb_url = f"{jenkins_url}/crumbIssuer/api/json"
        response = session.get(crumb_url, timeout=10)
        
        if response.status_code == 200:
            crumb_data = response.json()
            crumb_field = crumb_data.get('crumbRequestField', 'Jenkins-Crumb')
            crumb_value = crumb_data.get('crumb')
            
            if crumb_value:
                print_log(f"CSRF crumb obtained: {crumb_field}")
                return crumb_field, crumb_value
            else:
                print_log("CSRF protection may be disabled", "WARNING")
                return None, None
        else:
            print_log(f"Cannot get CSRF crumb: {response.status_code}", "WARNING")
            return None, None
            
    except Exception as e:
        print_log(f"CSRF crumb error: {e}", "WARNING")
        return None, None


def run_groovy_script(session, jenkins_url, groovy_script):
    """
    Run Groovy script via Jenkins script console with CSRF protection
    
    Args:
        session: requests.Session with auth
        jenkins_url: Jenkins base URL
        groovy_script: Groovy script content
        
    Returns:
        tuple: (success: bool, output: str)
    """
    try:
        url = f"{jenkins_url}/scriptText"
        
        # Get CSRF crumb
        crumb_field, crumb_value = get_crumb(session, jenkins_url)
        
        # Prepare headers and data
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'script': groovy_script}
        
        # Add crumb if available
        if crumb_field and crumb_value:
            headers[crumb_field] = crumb_value
            data[crumb_field] = crumb_value
            print_log("Using CSRF protection")
        else:
            print_log("No CSRF protection (may be disabled)")
        
        response = session.post(
            url,
            data=data,
            headers=headers,
            timeout=60
        )
        
        if response.status_code == 200:
            return True, response.text
        else:
            return False, f"HTTP {response.status_code}: {response.text[:500]}"
            
    except Exception as e:
        return False, str(e)


def create_credentials_via_groovy(session, jenkins_url, jenkins_scripts_file, config_values):
    """
    Create credentials using Groovy script with CSRF handling
    
    Args:
        session: requests.Session with auth
        jenkins_url: Jenkins base URL
        jenkins_scripts_file: Path to groovy scripts file
        config_values: Dict with credential values
        
    Returns:
        bool: True if successful
    """
    try:
        print_log("Creating credentials via Groovy script...")
        
        credentials_script = load_and_render_script(jenkins_scripts_file, 'credentials', config_values)
        
        success, output = run_groovy_script(session, jenkins_url, credentials_script)
        
        if success:
            print_log("Credentials creation result:")
            print_log(output)
            if "SUCCESS: AWS credentials created" in output and "SUCCESS: GitHub credentials created" in output:
                print_log("Both credentials created successfully")
                return True
            else:
                print_log("Credential creation may have issues", "WARNING")
                return False
        else:
            print_log(f"Failed to create credentials: {output}", "ERROR")
            return False
            
    except Exception as e:
        print_log(f"Error creating credentials: {e}", "ERROR")
        return False


def create_pipeline_job_with_webhook(session, jenkins_url, jenkins_scripts_file, config_values):
    """
    Create pipeline job with proper GitHub webhook configuration
    
    Args:
        session: requests.Session with auth
        jenkins_url: Jenkins base URL
        jenkins_scripts_file: Path to groovy scripts file
        config_values: Dict with pipeline configuration values
        
    Returns:
        bool: True if successful
    """
    try:
        print_log("Creating pipeline job with GitHub webhook trigger...")
    
        pipeline_script = load_and_render_script(jenkins_scripts_file, 'pipeline', config_values)
        
        # Job configuration values
        job_name = config_values['job_name']
        job_description = config_values['job_description']
        github_repo_url = config_values['github_repo']
        retention_days = config_values['build_retention_days']
        retention_count = config_values['build_retention_count']
        
        job_config = f'''<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>{job_description}</description>
  <keepDependencies>false</keepDependencies>
  <properties>
    <jenkins.model.BuildDiscarderProperty>
      <strategy class="hudson.tasks.LogRotator">
        <daysToKeep>{retention_days}</daysToKeep>
        <numToKeep>{retention_count}</numToKeep>
        <artifactDaysToKeep>-1</artifactDaysToKeep>
        <artifactNumToKeep>-1</artifactNumToKeep>
      </strategy>
    </jenkins.model.BuildDiscarderProperty>
    <com.coravy.hudson.plugins.github.GithubProjectProperty plugin="github">
      <projectUrl>{github_repo_url}</projectUrl>
      <displayName></displayName>
    </com.coravy.hudson.plugins.github.GithubProjectProperty>
    <org.jenkinsci.plugins.workflow.job.properties.PipelineTriggersJobProperty>
      <triggers>
        <com.cloudbees.jenkins.GitHubPushTrigger plugin="github">
          <spec></spec>
        </com.cloudbees.jenkins.GitHubPushTrigger>
      </triggers>
    </org.jenkinsci.plugins.workflow.job.properties.PipelineTriggersJobProperty>
  </properties>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script><![CDATA[{pipeline_script}]]></script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>'''
        
        # Get CSRF crumb
        crumb_field, crumb_value = get_crumb(session, jenkins_url)

        # Delete existing job
        delete_url = f"{jenkins_url}/job/{job_name}/doDelete"
        try:
            headers = {}
            if crumb_field and crumb_value:
                headers[crumb_field] = crumb_value
            session.post(delete_url, headers=headers, timeout=30)
            print_log("Removed existing job")
            time.sleep(2)
        except:
            pass

        # Create new job
        create_url = f"{jenkins_url}/createItem"
        headers = {'Content-Type': 'application/xml'}
        if crumb_field and crumb_value:
            headers[crumb_field] = crumb_value

        response = session.post(
            create_url,
            params={'name': job_name},
            data=job_config,
            headers=headers,
            timeout=60
        )

        if response.status_code in [200, 302]:
            print_log(f"Pipeline job '{job_name}' created with webhook trigger")
            time.sleep(2)
            
            # Initialize webhook trigger
            initialize_webhook_trigger(session, jenkins_url, job_name)
            verify_configuration(session, jenkins_url, config_values)
    
            print_log("=" * 70)
            print_log("GITHUB WEBHOOK CONFIGURATION")
            print_log("=" * 70)
            print_log(f"1. Go to: {github_repo_url}/settings/hooks")
            print_log(f"2. Click 'Add webhook' (or edit existing)")
            print_log(f"3. Webhook URL: {jenkins_url}/github-webhook/")
            print_log("4. Content type: application/json")
            print_log("5. Trigger: Just the push event")
            print_log("6. Webhook is now ACTIVE - test with a git push!")
            print_log("=" * 70)
        
            return True
        else:
            print_log(f"Failed to create job: {response.status_code}", "ERROR")
            print_log(f"Response: {response.text[:500]}", "ERROR")
            return False
        
    except Exception as e:
        print_log(f"Error creating pipeline: {str(e)}", "ERROR")
        return False


def initialize_webhook_trigger(session, jenkins_url, job_name):
    """
    Force Jenkins to recognize webhook trigger immediately after job creation
    
    Args:
        session: requests.Session with auth
        jenkins_url: Jenkins base URL
        job_name: Name of the pipeline job
    """
    try:
        print_log("Initializing webhook trigger...")
        
        time.sleep(3)
        
        # Reload job configuration
        reload_url = f"{jenkins_url}/job/{job_name}/reload"
        crumb_field, crumb_value = get_crumb(session, jenkins_url)
        headers = {}
        if crumb_field and crumb_value:
            headers[crumb_field] = crumb_value
        
        response = session.post(reload_url, headers=headers, timeout=30)
        if response.status_code in [200, 302]:
            print_log("Job configuration reloaded")
        
        # Trigger initial build
        print_log("Triggering initial build to activate webhook...")
        build_url = f"{jenkins_url}/job/{job_name}/build"
        
        response = session.post(build_url, headers=headers, timeout=30)
    
        if response.status_code in [200, 201, 302]:
            print_log("Initial build triggered successfully")
            print_log("Waiting for build to initialize (10 seconds)...")
            time.sleep(10)
            
            # Check if build started
            build_info_url = f"{jenkins_url}/job/{job_name}/lastBuild/api/json"
            try:
                build_response = session.get(build_info_url, timeout=10)
                if build_response.status_code == 200:
                    build_data = build_response.json()
                    print_log(f"Build #{build_data.get('number')} started")
                    print_log("Webhook trigger is now ACTIVE for future pushes!")
            except:
                print_log("Build queued - webhook will be active once it completes")
        else:
            print_log(f"Could not trigger initial build: {response.status_code}", "WARNING")
            print_log("Manually run one build, then webhook will work", "WARNING")
        
    except Exception as e:
        print_log(f"Error initializing webhook: {str(e)}", "ERROR")
        print_log("Solution: Run one manual build to activate webhook", "WARNING")


def verify_configuration(session, jenkins_url, config_values):
    """
    Verify all configuration is working
    
    Args:
        session: requests.Session with auth
        jenkins_url: Jenkins base URL
        config_values: Dict with configuration
        
    Returns:
        bool: True if verification successful
    """
    try:
        print_log("Verifying configuration...")
        
        # Check credentials
        cred_response = session.get(
            f"{jenkins_url}/credentials/store/system/domain/_/api/json",
            timeout=10
        )
        
        if cred_response.status_code == 200:
            creds_data = cred_response.json()
            cred_count = len(creds_data.get('credentials', []))
            print_log(f"Found {cred_count} credentials")
            
            for cred in creds_data.get('credentials', []):
                cred_id = cred.get('id', 'unknown')
                cred_desc = cred.get('description', 'No description')
                print_log(f"  - {cred_id}: {cred_desc}")
        else:
            print_log(f"Cannot verify credentials: {cred_response.status_code}", "WARNING")
        
        # Check job
        job_name = config_values['job_name']
        job_response = session.get(
            f"{jenkins_url}/job/{job_name}/api/json",
            timeout=10
        )
        
        if job_response.status_code == 200:
            job_data = job_response.json()
            job_name_result = job_data.get('name', 'unknown')
            job_desc = job_data.get('description', 'No description')
            print_log(f"Found job: {job_name_result} - {job_desc}")
        else:
            print_log(f"Cannot verify job: {job_response.status_code}", "WARNING")
            
        return True
        
    except Exception as e:
        print_log(f"Verification error: {e}", "WARNING")
        return False


def test_jenkins_connectivity(session, jenkins_url):
    """
    Test basic Jenkins connectivity and get version
    
    Args:
        session: requests.Session with auth
        jenkins_url: Jenkins base URL
        
    Returns:
        bool: True if Jenkins is accessible
    """
    try:
        response = session.get(f"{jenkins_url}/api/json", timeout=10)
        if response.status_code == 200:
            jenkins_data = response.json()
            print_log(f"Jenkins responding (version: {jenkins_data.get('version', 'unknown')})")
            return True
        else:
            print_log(f"Jenkins not responding: {response.status_code}", "ERROR")
            return False
    except Exception as e:
        print_log(f"Cannot connect to Jenkins: {e}", "ERROR")
        return False


def run_jenkins_pipeline_configuration(region, config_values):
    """
    Main orchestration function for Jenkins pipeline configuration
    
    Args:
        region: AWS region
        config_values: Dict with all configuration values
        
    Returns:
        bool: True if successful
    """
    print_log("=" * 70)
    print_log("JENKINS PIPELINE CONFIGURATION STARTING")
    print_log("=" * 70)
    
    try:
        # Initialize AWS EC2 client
        ec2_client = boto3.client('ec2', region_name=region)
        
        # Find Jenkins instance
        print_log("Finding Jenkins instance...")
        jenkins_url, instance_id = find_jenkins_instance(ec2_client)
        if not jenkins_url:
            print_log("Jenkins instance not found", "ERROR")
            return False
        
        # Create requests session with auth
        session = requests.Session()
        session.auth = HTTPBasicAuth(config_values['jenkins_user'], config_values['jenkins_password'])
        
        # Test connectivity
        print_log("Testing Jenkins connectivity...")
        if not test_jenkins_connectivity(session, jenkins_url):
            return False
        
        # Test CSRF
        print_log("CSRF Protection Check...")
        crumb_field, crumb_value = get_crumb(session, jenkins_url)
        if crumb_field:
            print_log(f"CSRF protection active: {crumb_field}")
        else:
            print_log("CSRF protection disabled", "WARNING")
        
        # Create credentials
        print_log("Creating Credentials...")
        if not create_credentials_via_groovy(session, jenkins_url, config_values['jenkins_scripts_file'], config_values):
            print_log("Failed to create credentials", "ERROR")
            return False
        
        # Create pipeline job
        print_log("Creating Pipeline Job with Webhook...")
        if not create_pipeline_job_with_webhook(session, jenkins_url, config_values['jenkins_scripts_file'], config_values):
            print_log("Failed to create pipeline job", "ERROR")
            return False
        
        # Success
        print_log("=" * 70)
        print_log("JENKINS PIPELINE CONFIGURATION COMPLETE!")
        print_log("=" * 70)
        print_log(f"Jenkins URL: {jenkins_url}")
        print_log(f"Region: {region}")
        print_log(f"S3 Bucket: {config_values['s3_bucket']}")
        print_log(f"CodeBuild Project: {config_values['codebuild_project_name']}")
        print_log("")
        print_log("Configured:")
        print_log("  AWS credentials (aws-credentials)")
        print_log("  GitHub credentials (github-credentials)")
        print_log(f"  {config_values['job_name']} with webhook trigger")
        print_log("")
        print_log("Jenkins pipeline is ready!")
        return True
    
    except Exception as e:
        print_log(f"Error during configuration: {e}", "ERROR")
        return False


def main():
    """Main entry point when run as script"""
    import sys
    import os
    import argparse
    
    # Add parent directory to path to import readconfig
    sys.path.append(str(Path(__file__).parent.parent))
    from readconfig import get_config_value
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Configure Jenkins pipeline with credentials and jobs')
    parser.add_argument('--non-interactive', action='store_true',
                       help='Run without user confirmation (for automation)')
    args = parser.parse_args()
    
    print("=" * 70)
    print("JENKINS PIPELINE CONFIGURATION")
    print("Reads configuration from config.ini")
    print("Loads Groovy scripts from single file using markers")
    print("Supports templating for dynamic values")
    print("=" * 70)
    print()
    
    # Skip confirmation if running in non-interactive mode
    if not args.non_interactive:
        confirm = input("Do you want to continue? (y/N): ").strip().lower()
        if confirm != 'y':
            print("Configuration cancelled.")
            return
    
    # Read all configuration values
    config_values = {
        'region': get_config_value('AWS', 'region'),
        'jenkins_user': get_config_value('Jenkins', 'jenkins_user'),
        'jenkins_password': get_config_value('Jenkins', 'jenkins_password'),
        'aws_access_key': get_config_value('Credentials', 'aws_access_key'),
        'aws_secret_key': get_config_value('Credentials', 'aws_secret_key'),
        'github_username': get_config_value('Credentials', 'github_username'),
        'github_token': get_config_value('Credentials', 'github_token'),
        'jenkins_scripts_file': get_config_value('Scripts', 'jenkins_scripts_file'),
        'codebuild_project_name': get_config_value('CodeBuild', 'project_name'),
        'codedeploy_app_name': get_config_value('CodeDeploy', 'app_name'),
        'codedeploy_deployment_group': get_config_value('CodeDeploy', 'deployment_group_name'),
        'code_deploy_infra': get_config_value('CodeDeploy', 'code_deploy_infra'),
        's3_bucket': get_config_value('S3', 'bucket_name'),
        'github_repo': get_config_value('CodeBuild', 'github_repo'),
        'github_branch': get_config_value('Pipeline', 'github_branch'),
        'application_url': get_config_value('Pipeline', 'application_url'),
        'job_name': get_config_value('Pipeline', 'job_name'),
        'job_description': get_config_value('Pipeline', 'job_description'),
        'build_retention_days': get_config_value('Pipeline', 'build_retention_days'),
        'build_retention_count': get_config_value('Pipeline', 'build_retention_count'),
    }
    
    success = run_jenkins_pipeline_configuration(config_values['region'], config_values)
    
    if success:
        print("\nSUCCESS! - Jenkins pipeline is ready!")
        sys.exit(0)
    else:
        print("\nSome issues remain - check logs above")
        sys.exit(1)


if __name__ == "__main__":
    main()