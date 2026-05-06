#!/usr/bin/env groovy
// ============================================================
// MARKER: CREDENTIALS_START
// ============================================================
import jenkins.model.*
import com.cloudbees.plugins.credentials.*
import com.cloudbees.plugins.credentials.domains.*
import com.cloudbees.plugins.credentials.impl.*
import com.cloudbees.jenkins.plugins.awscredentials.AWSCredentialsImpl
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl

def instance = Jenkins.getInstance()
def domain = Domain.global()
def store = instance.getExtensionList('com.cloudbees.plugins.credentials.SystemCredentialsProvider')[0].getStore()

// Remove existing credentials if they exist
def existingCredentials = store.getCredentials(domain)
existingCredentials.each { cred ->
    if (cred.getId() == "aws-credentials" || cred.getId() == "github-credentials") {
        store.removeCredentials(domain, cred)
        println "Removed existing credential: " + cred.getId()
    }
}


// Create AWS credentials using proper AWS credential type
def awsCreds = new AWSCredentialsImpl(
    CredentialsScope.GLOBAL,
    "aws-credentials",
    "{{aws_access_key}}",
    "{{aws_secret_key}}",
    "AWS Access Keys for CI/CD Pipeline"
)

store.addCredentials(domain, awsCreds)
println "SUCCESS: AWS credentials created"

// Create GitHub credentials  
def githubCreds = new UsernamePasswordCredentialsImpl(
    CredentialsScope.GLOBAL,
    "github-credentials", 
    "GitHub Username and Personal Access Token",
    "{{github_username}}",
    "{{github_token}}"
)

store.addCredentials(domain, githubCreds)
println "SUCCESS: GitHub credentials created"

// Save changes
instance.save()

// Verify credentials were created
def allCreds = store.getCredentials(domain)
println "Total credentials: " + allCreds.size()
allCreds.each { cred ->
    println "- ID: " + cred.getId() + ", Description: " + cred.getDescription()
}
// ============================================================
// MARKER: CREDENTIALS_END
// ============================================================

// ============================================================
// MARKER: DISABLE_CSRF_START
// ============================================================

#!/usr/bin/env groovy
import jenkins.model.Jenkins
import hudson.security.csrf.DefaultCrumbIssuer

def instance = Jenkins.getInstance()
instance.setCrumbIssuer(null)
instance.save()
println "CSRF protection disabled"
// ============================================================
// MARKER: DISABLE_CSRF_END
// ============================================================

// ============================================================
// MARKER: PIPELINE_START
// ============================================================
pipeline {
    agent any
    
    environment {
        AWS_REGION = '{{aws_region}}'
        CODEBUILD_PROJECT_NAME = '{{codebuild_project}}'
        CODEDEPLOY_APPLICATION_NAME = '{{codedeploy_app}}'
        CODEDEPLOY_DEPLOYMENT_GROUP = '{{codedeploy_group}}'
        S3_BUCKET = '{{s3_bucket}}'
        GITHUB_REPO = '{{github_repo}}'
        GITHUB_BRANCH = '{{github_branch}}'
        CODE_DEPLOY_INFRA = '{{code_deploy_infra}}'.toLowerCase().trim()
    }
    
    stages {
        stage('Checkout Code') {
            steps {
                script {
                    echo "Starting pipeline for HelloWorld application..."
                    echo "CODE_DEPLOY_INFRA value: '${env.CODE_DEPLOY_INFRA}'"
                    echo "Pipeline Mode: ${env.CODE_DEPLOY_INFRA == 'true' ? 'Week 1 (EC2 + CodeDeploy)' : 'Week 2+ (CodeBuild only)'}"
                    echo "Checking out code from GitHub..."
                }
                checkout([
                    $class: 'GitSCM',
                    branches: [[name: "${GITHUB_BRANCH}"]],
                    userRemoteConfigs: [[
                        credentialsId: 'github-credentials',
                        url: "${GITHUB_REPO}"
                    ]]
                ])
                script {
                    echo "Code checkout completed successfully"
                    sh 'ls -la'
                }
            }
        }
        
        stage('Trigger CodeBuild') {
            steps {
                script {
                    echo "Starting CodeBuild process using AWS CLI..."
                    def buildId = sh(
                        script: """
                            aws codebuild start-build \\
                                --project-name ${CODEBUILD_PROJECT_NAME} \\
                                --region ${AWS_REGION} \\
                                --query 'build.id' \\
                                --output text
                        """,
                        returnStdout: true
                    ).trim()
                    echo "CodeBuild started with ID: ${buildId}"
                    env.CODEBUILD_ID = buildId
                    
                    def buildStatus = ''
                    def maxAttempts = 60
                    def attempt = 0
                    
                    while (attempt < maxAttempts) {
                        attempt++
                        buildStatus = sh(
                            script: """
                                aws codebuild batch-get-builds \\
                                    --ids ${buildId} \\
                                    --region ${AWS_REGION} \\
                                    --query 'builds[0].buildStatus' \\
                                    --output text
                            """,
                            returnStdout: true
                        ).trim()
                        echo "Build attempt ${attempt}/${maxAttempts}: Status = ${buildStatus}"
                        
                        if (buildStatus == 'SUCCEEDED') {
                            echo "CodeBuild completed successfully!"
                            break
                        } else if (buildStatus in ['FAILED', 'STOPPED', 'FAULT', 'TIMED_OUT']) {
                            sh """
                                aws codebuild batch-get-builds \\
                                    --ids ${buildId} \\
                                    --region ${AWS_REGION} \\
                                    --query 'builds[0].phases[*].{Phase:phaseType,Status:phaseStatus,Duration:durationInSeconds}' \\
                                    --output table
                            """
                            error("CodeBuild failed with status: ${buildStatus}")
                        }
                        sleep(time: 10, unit: 'SECONDS')
                    }
                    
                    if (attempt >= maxAttempts && buildStatus != 'SUCCEEDED') {
                        error("CodeBuild timed out. Final status: ${buildStatus}")
                    }
                }
            }
        }
        
        stage('Wait for Build Artifact') {
            steps {
                script {
                    echo "Waiting for build artifact to be available in S3..."
                    sleep(time: 30, unit: 'SECONDS')
                    sh """
                        aws s3 ls s3://${S3_BUCKET}/codebuild-artifact.zip --region ${AWS_REGION}
                    """
                    echo "Build artifact verified in S3"
                }
            }
        }
        
        stage('Deploy with CodeDeploy') {
            when {
                expression { env.CODE_DEPLOY_INFRA == 'true' }
            }
            steps {
                script {
                    echo "Starting CodeDeploy deployment..."
                    def timestamp = new Date().format("yyyy-MM-dd-HH-mm-ss")
                    def deploymentId = sh(
                        script: """
                            aws deploy create-deployment \\
                                --application-name ${CODEDEPLOY_APPLICATION_NAME} \\
                                --deployment-group-name ${CODEDEPLOY_DEPLOYMENT_GROUP} \\
                                --s3-location bucket=${S3_BUCKET},key=codebuild-artifact.zip,bundleType=zip \\
                                --deployment-config-name CodeDeployDefault.OneAtATime \\
                                --description "Jenkins deployment ${timestamp}" \\
                                --region ${AWS_REGION} \\
                                --query 'deploymentId' \\
                                --output text
                        """,
                        returnStdout: true
                    ).trim()
                    echo "CodeDeploy deployment started with ID: ${deploymentId}"
                    env.DEPLOYMENT_ID = deploymentId
                }
            }
        }
        
        stage('Monitor Deployment') {
            when {
                expression { env.CODE_DEPLOY_INFRA == 'true' }
            }
            steps {
                script {
                    echo "Monitoring deployment progress..."
                    def maxAttempts = 30
                    def attempt = 0
                    def deploymentStatus = ''
                    
                    while (attempt < maxAttempts) {
                        attempt++
                        deploymentStatus = sh(
                            script: """
                                aws deploy get-deployment \\
                                    --deployment-id ${env.DEPLOYMENT_ID} \\
                                    --region ${AWS_REGION} \\
                                    --query 'deploymentInfo.status' \\
                                    --output text
                            """,
                            returnStdout: true
                        ).trim()
                        echo "Deployment attempt ${attempt}/${maxAttempts}: Status = ${deploymentStatus}"
                        
                        if (deploymentStatus == 'Succeeded') {
                            echo "Deployment completed successfully!"
                            break
                        } else if (deploymentStatus in ['Failed', 'Stopped']) {
                            error("Deployment failed with status: ${deploymentStatus}")
                        }
                        sleep(time: 20, unit: 'SECONDS')
                    }
                    
                    if (attempt >= maxAttempts && deploymentStatus != 'Succeeded') {
                        error("Deployment timed out. Final status: ${deploymentStatus}")
                    }
                }
            }
        }
        
        stage('Verify Application') {
            when {
                expression { env.CODE_DEPLOY_INFRA == 'true' }
            }
            steps {
                script {
                    echo "Verifying application deployment..."
                    sleep(time: 30, unit: 'SECONDS')
                    
                    // Get the public IP of the tomcat-servers instance
                    def tomcatPublicIp = sh(
                        script: """
                            aws ec2 describe-instances \\
                                --filters "Name=tag:Name,Values=tomcat-servers" "Name=instance-state-name,Values=running" \\
                                --region ${AWS_REGION} \\
                                --query 'Reservations[0].Instances[0].PublicIpAddress' \\
                                --output text
                        """,
                        returnStdout: true
                    ).trim()
                    
                    echo "Tomcat Server Public IP: ${tomcatPublicIp}"
                    
                    def applicationUrl = "http://${tomcatPublicIp}:8080/HelloWorld-1/"
                    echo "Application URL: ${applicationUrl}"
                    
                    sh """
                        curl -f -s -o /dev/null -w "HTTP Status: %{http_code}\\n" ${applicationUrl} || echo "Application not yet accessible"
                    """
                    
                    echo "============================================================"
                    echo "Deployment Successful!"
                    echo "Access your application at: ${applicationUrl}"
                    echo "============================================================"
                }
            }
        }
        
        stage('Week 2+ Completion') {
            when {
                expression { env.CODE_DEPLOY_INFRA == 'false' }
            }
            steps {
                script {
                    echo "============================================================"
                    echo "Week 2 Mode: CodeBuild completed successfully"
                    echo "Artifact location: s3://${S3_BUCKET}/codebuild-artifact.zip"
                    echo "============================================================"
                }
            }
        }
    }
    
    post {
        success {
            script {
                if (env.CODE_DEPLOY_INFRA == 'true') {
                    // Get tomcat server IP for final message
                    try {
                        def tomcatPublicIp = sh(
                            script: """
                                aws ec2 describe-instances \\
                                    --filters "Name=tag:Name,Values=tomcat-servers" "Name=instance-state-name,Values=running" \\
                                    --region ${AWS_REGION} \\
                                    --query 'Reservations[0].Instances[0].PublicIpAddress' \\
                                    --output text
                            """,
                            returnStdout: true
                        ).trim()
                        
                        echo "============================================================"
                        echo "SUCCESS: HelloWorld application built and deployed!"
                        echo "============================================================"
                        echo "Access your application at: http://${tomcatPublicIp}:8080/HelloWorld-1/"
                        echo "============================================================"
                    } catch (Exception e) {
                        echo "SUCCESS: HelloWorld application built and deployed to EC2 via CodeDeploy!"
                    }
                } else {
                    echo "SUCCESS: HelloWorld application built and artifact uploaded to S3!"
                }
            }
        }
        failure {
            echo "FAILURE: Pipeline failed!"
        }
        always {
            echo "Pipeline execution completed."
        }
    }
}
// ============================================================
// MARKER: PIPELINE_END
// ============================================================
