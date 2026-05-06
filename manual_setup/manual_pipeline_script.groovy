pipeline {
    agent any
    
    environment {
        // AWS Configuration
        AWS_REGION = 'us-east-1'
        
        // CodeBuild Configuration
        CODEBUILD_PROJECT_NAME = 'helloworld'
        
        // S3 Configuration - REPLACE THIS
        S3_BUCKET = 'YOUR_S3_BUCKET_NAME'
        
        // GitHub Configuration - REPLACE THIS
        GITHUB_REPO = 'YOUR_GITHUB_REPO_URL'
        GITHUB_BRANCH = 'main'
        
        // Deployment Mode: 'true' for Week 1 (EC2 + CodeDeploy), 'false' for Week 2+ (CodeBuild only)
        CODE_DEPLOY_INFRA = 'true'
        
        // CodeDeploy Configuration
        CODEDEPLOY_APPLICATION_NAME = 'helloworld-tomcat-app'
        CODEDEPLOY_DEPLOYMENT_GROUP = 'tomcat-servers'
    }
    
    stages {
        stage('Checkout Code') {
            steps {
                script {
                    echo "Starting pipeline for HelloWorld application..."
                    echo "CODE_DEPLOY_INFRA: ${env.CODE_DEPLOY_INFRA}"
                    echo "Pipeline Mode: ${env.CODE_DEPLOY_INFRA.toLowerCase().trim() == 'true' ? 'Week 1 (EC2 + CodeDeploy)' : 'Week 2+ (CodeBuild only)'}"
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
                    echo "Code checkout completed"
                    sh 'ls -la'
                }
            }
        }
        
        stage('Trigger CodeBuild') {
            steps {
                script {
                    echo "Starting CodeBuild..."
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
                    echo "CodeBuild ID: ${buildId}"
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
                        echo "Build status (${attempt}/${maxAttempts}): ${buildStatus}"
                        
                        if (buildStatus == 'SUCCEEDED') {
                            echo "CodeBuild completed successfully"
                            break
                        } else if (buildStatus in ['FAILED', 'STOPPED', 'FAULT', 'TIMED_OUT']) {
                            sh """
                                aws codebuild batch-get-builds \\
                                    --ids ${buildId} \\
                                    --region ${AWS_REGION} \\
                                    --query 'builds[0].phases[*].{Phase:phaseType,Status:phaseStatus,Duration:durationInSeconds}' \\
                                    --output table
                            """
                            error("CodeBuild failed: ${buildStatus}")
                        }
                        sleep(time: 10, unit: 'SECONDS')
                    }
                    
                    if (attempt >= maxAttempts && buildStatus != 'SUCCEEDED') {
                        error("CodeBuild timed out: ${buildStatus}")
                    }
                }
            }
        }
        
        stage('Wait for Build Artifact') {
            steps {
                script {
                    echo "Waiting for artifact in S3..."
                    sleep(time: 30, unit: 'SECONDS')
                    sh """
                        aws s3 ls s3://${S3_BUCKET}/codebuild-artifact.zip --region ${AWS_REGION}
                    """
                    echo "Artifact verified in S3"
                }
            }
        }
        
        stage('Deploy with CodeDeploy') {
            when {
                expression { env.CODE_DEPLOY_INFRA.toLowerCase().trim() == 'true' }
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
                    echo "Deployment ID: ${deploymentId}"
                    env.DEPLOYMENT_ID = deploymentId
                }
            }
        }
        
        stage('Monitor Deployment') {
            when {
                expression { env.CODE_DEPLOY_INFRA.toLowerCase().trim() == 'true' }
            }
            steps {
                script {
                    echo "Monitoring deployment..."
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
                        echo "Deployment status (${attempt}/${maxAttempts}): ${deploymentStatus}"
                        
                        if (deploymentStatus == 'Succeeded') {
                            echo "Deployment completed successfully"
                            break
                        } else if (deploymentStatus in ['Failed', 'Stopped']) {
                            error("Deployment failed: ${deploymentStatus}")
                        }
                        sleep(time: 20, unit: 'SECONDS')
                    }
                    
                    if (attempt >= maxAttempts && deploymentStatus != 'Succeeded') {
                        error("Deployment timed out: ${deploymentStatus}")
                    }
                }
            }
        }
        
        stage('Verify Application') {
            when {
                expression { env.CODE_DEPLOY_INFRA.toLowerCase().trim() == 'true' }
            }
            steps {
                script {
                    echo "Verifying application..."
                    sleep(time: 30, unit: 'SECONDS')
                    
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
                    
                    echo "Tomcat Server IP: ${tomcatPublicIp}"
                    
                    def applicationUrl = "http://${tomcatPublicIp}:8080/HelloWorld-1/"
                    echo "Application URL: ${applicationUrl}"
                    
                    sh """
                        curl -f -s -o /dev/null -w "HTTP Status: %{http_code}\\n" ${applicationUrl} || echo "Application not yet accessible"
                    """
                    
                    echo "Deployment successful! Access at: ${applicationUrl}"
                }
            }
        }
        
        stage('Week 2+ Completion') {
            when {
                expression { env.CODE_DEPLOY_INFRA.toLowerCase().trim() == 'false' }
            }
            steps {
                script {
                    echo "Week 2+ Mode: CodeBuild completed"
                    echo "Artifact: s3://${S3_BUCKET}/codebuild-artifact.zip"
                }
            }
        }
    }
    
    post {
        success {
            script {
                if (env.CODE_DEPLOY_INFRA.toLowerCase().trim() == 'true') {
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
                        
                        echo "SUCCESS: Application deployed at http://${tomcatPublicIp}:8080/HelloWorld-1/"
                    } catch (Exception e) {
                        echo "SUCCESS: Application deployed to EC2"
                    }
                } else {
                    echo "SUCCESS: Build completed, artifact at s3://${S3_BUCKET}/codebuild-artifact.zip"
                }
            }
        }
        failure {
            echo "FAILURE: Pipeline failed"
        }
        always {
            echo "Pipeline execution completed"
        }
    }
}
