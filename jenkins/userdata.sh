# ===== JENKINS_USERDATA_START =====
#!/bin/bash
exec > /var/log/jenkins-setup.log 2>&1
echo "Starting Jenkins setup: $(date)"

# Install essentials
export DEBIAN_FRONTEND=noninteractive
apt update && apt upgrade -y
apt install -y openjdk-21-jdk wget curl unzip git maven

# Install AWS CLI
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && ./aws/install && rm -rf aws awscliv2.zip

# Install Jenkins from WAR file (more reliable than repo-based installation)
echo "Installing Jenkins from WAR file..."
id jenkins >/dev/null 2>&1 || useradd -m -s /bin/bash jenkins
mkdir -p /opt/jenkins /var/lib/jenkins /var/cache/jenkins
cd /opt/jenkins

# Download Jenkins WAR
echo "Downloading Jenkins WAR..."
wget -q https://mirrors.jenkins.io/war-stable/latest/jenkins.war || {
    echo "Mirror failed, trying updates.jenkins.io..."
    wget -q https://updates.jenkins.io/download/war/latest/jenkins.war
}

if [ ! -f jenkins.war ]; then
    echo "ERROR: Failed to download Jenkins WAR"
    exit 1
fi

echo "Jenkins WAR downloaded successfully"
chown jenkins:jenkins jenkins.war

# Create Jenkins systemd service
echo "Creating Jenkins systemd service..."
cat > /etc/systemd/system/jenkins.service << 'SYSTEMD_EOF'
[Unit]
Description=Jenkins
After=network.target

[Service]
Type=notify
NotifyAccess=main
User=jenkins
Group=jenkins
Environment="JENKINS_HOME=/var/lib/jenkins"
WorkingDirectory=/var/lib/jenkins
ExecStart=/usr/bin/java -Xmx1024m -Xms256m -DJENKINS_HOME=/var/lib/jenkins -jar /opt/jenkins/jenkins.war --httpPort=8080 --prefix=/ --webroot=/var/cache/jenkins
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
KillSignal=SIGTERM
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

# Pre-configure Jenkins BEFORE starting
echo "Pre-configuring Jenkins..."
mkdir -p /var/lib/jenkins/init.groovy.d

# Disable setup wizard and skip initial setup (must be done before Jenkins starts)
echo 'jenkins.install.runSetupWizard=false' > /var/lib/jenkins/jenkins.install.UpgradeWizard.state
echo '2.541.3' > /var/lib/jenkins/jenkins.install.InstallUtil.lastExecVersion

# Init script 1: Create admin user and configure security
cat > /var/lib/jenkins/init.groovy.d/01-create-admin.groovy << 'GROOVYEOF'
import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import java.util.logging.Logger

def logger = Logger.getLogger("")
try {
    def instance = Jenkins.getInstance()
    def realm = new HudsonPrivateSecurityRealm(false)
    instance.setSecurityRealm(realm)

    // Always create/update admin user
    def existingUser = realm.getUser("{{JENKINS_USER}}")
    if (existingUser != null) {
        existingUser.delete()
        logger.info("Removed existing user for re-creation")
    }
    realm.createAccount("{{JENKINS_USER}}", "{{JENKINS_PASSWORD}}")
    logger.info("Admin user created: {{JENKINS_USER}}")

    // Set authorization strategy
    def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
    strategy.setAllowAnonymousRead(false)
    instance.setAuthorizationStrategy(strategy)

    instance.save()
    logger.info("Jenkins security configured")
} catch (Exception e) {
    logger.severe("Error configuring security: " + e.message)
}
GROOVYEOF

# Write Jenkins URL config file directly (required for CLI to work - must be done before Jenkins starts)
# This is more reliable than a Groovy init script which runs AFTER startup
PUBLIC_IP=$(curl -s -m 10 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "localhost")
echo "Instance public IP: ${PUBLIC_IP}"
cat > /var/lib/jenkins/jenkins.model.JenkinsLocationConfiguration.xml << XMLEOF
<?xml version='1.0' encoding='UTF-8'?>
<jenkins.model.JenkinsLocationConfiguration>
  <jenkinsUrl>http://${PUBLIC_IP}:8080/</jenkinsUrl>
  <adminAddress>address not configured yet &lt;nobody@nowhere&gt;</adminAddress>
</jenkins.model.JenkinsLocationConfiguration>
XMLEOF
echo "✓ Jenkins URL pre-configured: http://${PUBLIC_IP}:8080/"

# Install plugins BEFORE starting Jenkins (avoids Jenkins CLI auth/origin issues)
echo "Downloading Plugin Installation Manager Tool..."
wget -q "https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.12.14/jenkins-plugin-manager-2.12.14.jar" \
    -O /tmp/jenkins-plugin-manager.jar && echo "✓ PIMT downloaded" || {
    echo "ERROR: Failed to download Plugin Manager Tool"
    exit 1
}

echo "Installing plugins via PIMT (this may take a few minutes)..."
java -jar /tmp/jenkins-plugin-manager.jar \
    --war /opt/jenkins/jenkins.war \
    --plugin-download-directory /var/lib/jenkins/plugins \
    --plugins git github credentials workflow-aggregator build-timeout timestamper ws-cleanup codedeploy aws-codebuild aws-credentials pipeline-aws \
    2>&1 | tee /var/log/plugin-install.log
PLUGIN_COUNT=$(ls /var/lib/jenkins/plugins/*.jpi 2>/dev/null | wc -l)
echo "✓ Installed ${PLUGIN_COUNT} plugins"

# Set proper permissions on all jenkins directories
chown -R jenkins:jenkins /opt/jenkins /var/lib/jenkins /var/cache/jenkins

# Start Jenkins (ONCE - all pre-configuration and plugins already in place)
echo "Starting Jenkins service..."
systemctl daemon-reload
systemctl enable jenkins
systemctl start jenkins

# Wait for Jenkins to be fully ready (extended timeout)
echo "Waiting for Jenkins to start (up to 180 seconds)..."
JENKINS_READY=0
for i in {1..36}; do
    if curl -s -f http://localhost:8080/login >/dev/null 2>&1; then
        echo "✓ Jenkins is responding on port 8080"
        JENKINS_READY=1
        break
    fi
    echo "  Attempt $i/36 - waiting for Jenkins to respond..."
    sleep 5
done

if [ $JENKINS_READY -eq 0 ]; then
    echo "ERROR: Jenkins failed to start within timeout"
    systemctl status jenkins --no-pager | tail -20
    tail -50 /var/log/jenkins/jenkins.log || tail -50 /var/log/jenkins-setup.log
    exit 1
fi

# Additional wait for security realm initialization
echo "Waiting for security initialization..."
sleep 10

# Status report
PUBLIC_IP=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "unknown")
PLUGIN_COUNT=$(ls /var/lib/jenkins/plugins/*.jpi 2>/dev/null | wc -l || echo "0")

echo "=========================================="
echo "Jenkins setup completed: $(date)"
echo "Jenkins URL: http://${PUBLIC_IP}:8080"
echo "Admin User: {{JENKINS_USER}}"
echo "Total plugins installed: $PLUGIN_COUNT"
echo "=========================================="

# Create completion marker
touch /var/lib/jenkins/setup-complete.txt
echo "Jenkins setup completed at $(date)" >> /var/lib/jenkins/setup-complete.txt
# ===== JENKINS_USERDATA_END =====


# ===== TOMCAT_USERDATA_START =====

#!/bin/bash
# Log everything for debugging
exec > >(tee /var/log/user-data.log) 2>&1
echo "Starting Debian user data script at $(date)"

# Update system
apt-get update -y

# Install required packages for Debian 13
apt-get install -y awscli unzip default-jdk wget curl tomcat10

# Set JAVA_HOME for default-jdk on Debian 13
export JAVA_HOME=/usr/lib/jvm/default-java
echo 'export JAVA_HOME=/usr/lib/jvm/default-java' >> /etc/environment

# Set JAVA_HOME for Tomcat service
echo 'JAVA_HOME=/usr/lib/jvm/default-java' >> /etc/default/tomcat10

# Enable and start Tomcat10 service (using package's default service)
systemctl enable tomcat10
systemctl start tomcat10

echo "Waiting for Tomcat to start..."
sleep 30

# Function to deploy from S3
deploy_application() {
    local S3_BUCKET="{{S3_BUCKET}}"
    local ARTIFACT_KEY="{{ARTIFACT_KEY}}"
    local WEBAPPS_DIR="{{WEBAPPS_DIR}}"
    local TEMP_DIR="/tmp/deploy"
    local AWS_REGION="{{AWS_REGION}}"
    
    echo "Starting deployment from S3 at $(date)"
    
    # Create temp directory
    mkdir -p $TEMP_DIR
    cd $TEMP_DIR
    
    # Download artifact from S3
    echo "Downloading artifact from S3..."
    aws s3 cp s3://$S3_BUCKET/$ARTIFACT_KEY ./artifact.zip --region $AWS_REGION
    
    if [ $? -eq 0 ]; then
        echo "Successfully downloaded artifact"
        
        # Stop Tomcat temporarily
        systemctl stop tomcat10
        sleep 10
        
        # Clean old HelloWorld deployments
        rm -rf $WEBAPPS_DIR/HelloWorld*
        
        # Extract artifact
        echo "Extracting artifact..."
        unzip -o artifact.zip
        
        # Find and copy WAR files
        cp *.war $WEBAPPS_DIR/
        
        # Set proper permissions (tomcat10 user/group created by package)
        chown -R {{TOMCAT_USER}}:{{TOMCAT_GROUP}} $WEBAPPS_DIR
        
        # Start Tomcat
        systemctl start tomcat10
        
        echo "Deployment completed at $(date)"
        echo "Deployed applications:"
        ls -la $WEBAPPS_DIR/
        
        # Cleanup
        rm -rf $TEMP_DIR
        
    else
        echo "ERROR: Failed to download artifact from S3"
        # Start Tomcat anyway
        systemctl start tomcat10
    fi
}

# Deploy application
deploy_application

# Create deployment script for future updates
cat > /opt/deploy-from-s3.sh << 'DEPLOYEOF'
#!/bin/bash
S3_BUCKET="{{S3_BUCKET}}"
ARTIFACT_KEY="{{ARTIFACT_KEY}}"
WEBAPPS_DIR="{{WEBAPPS_DIR}}"
TEMP_DIR="/tmp/deploy"
AWS_REGION="{{AWS_REGION}}"

echo "Starting deployment from S3 at $(date)" >> /var/log/deployment.log

mkdir -p $TEMP_DIR
cd $TEMP_DIR

# Download latest artifact
aws s3 cp s3://$S3_BUCKET/$ARTIFACT_KEY ./artifact.zip --region $AWS_REGION

if [ $? -eq 0 ]; then
    # Stop Tomcat
    systemctl stop tomcat10
    sleep 10
    
    # Clean and deploy
    rm -rf $WEBAPPS_DIR/HelloWorld*
    unzip -o artifact.zip
    cp *.war $WEBAPPS_DIR/
    chown -R {{TOMCAT_USER}}:{{TOMCAT_GROUP}} $WEBAPPS_DIR
    
    # Start Tomcat
    systemctl start tomcat10
    
    echo "Deployment completed at $(date)" >> /var/log/deployment.log
    rm -rf $TEMP_DIR
else
    echo "ERROR: Failed to download from S3 at $(date)" >> /var/log/deployment.log
    systemctl start tomcat10
fi
DEPLOYEOF

chmod +x /opt/deploy-from-s3.sh

# Verify installation
echo "Tomcat10 status:"
systemctl status tomcat10

echo "Java version:"
java -version

echo "Listening ports:"
ss -tlnp | grep 8080

echo "Tomcat webapps directory:"
ls -la {{WEBAPPS_DIR}}/

echo "User data script completed at $(date)"
# ===== TOMCAT_USERDATA_END =====


# ===== TOMCAT_CODEDEPLOY_USERDATA_START =====

#!/bin/bash
apt update && apt upgrade -y
apt install openjdk-21-jdk -y
apt install -y tomcat10
apt install -y ruby-full wget

cd /home/admin
apt install -y alien
wget https://aws-codedeploy-{{AWS_REGION}}.s3.{{AWS_REGION}}.amazonaws.com/latest/codedeploy-agent.noarch.rpm
alien -d codedeploy-agent.noarch.rpm
dpkg -i codedeploy-agent_*_all.deb

systemctl start codedeploy-agent
systemctl enable codedeploy-agent
systemctl start tomcat10
systemctl enable tomcat10

# ===== TOMCAT_CODEDEPLOY_USERDATA_END =====
