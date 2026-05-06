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

# Add Jenkins repo and install
curl -fsSL https://pkg.jenkins.io/debian-stable/jenkins.io-2023.key | tee /usr/share/keyrings/jenkins-keyring.asc > /dev/null
echo deb [signed-by=/usr/share/keyrings/jenkins-keyring.asc] https://pkg.jenkins.io/debian-stable binary/ | tee /etc/apt/sources.list.d/jenkins.list > /dev/null
apt update && apt install -y jenkins
systemctl stop jenkins

# Pre-configure Jenkins
mkdir -p /var/lib/jenkins/init.groovy.d
echo 'jenkins.install.runSetupWizard=false' > /var/lib/jenkins/jenkins.install.UpgradeWizard.state
echo '2.0' > /var/lib/jenkins/jenkins.install.InstallUtil.lastExecVersion

# Basic config
cat > /var/lib/jenkins/config.xml << 'EOF'
<?xml version='1.1' encoding='UTF-8'?>
<hudson>
  <version>2.0</version>
  <installStateName>INITIAL_SETUP_COMPLETED</installStateName>
  <numExecutors>2</numExecutors>
  <useSecurity>true</useSecurity>
  <authorizationStrategy class="hudson.security.FullControlOnceLoggedInAuthorizationStrategy">
    <denyAnonymousReadAccess>true</denyAnonymousReadAccess>
  </authorizationStrategy>
  <securityRealm class="hudson.security.HudsonPrivateSecurityRealm">
    <disableSignup>true</disableSignup>
  </securityRealm>
</hudson>
EOF

# Admin user setup only - no plugin installation via Groovy
cat > /var/lib/jenkins/init.groovy.d/01-admin.groovy << 'EOF'
import jenkins.model.*
import hudson.security.*
import jenkins.install.InstallState
def instance = Jenkins.getInstance()
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
def realm = new HudsonPrivateSecurityRealm(false)
instance.setSecurityRealm(realm)
realm.createAccount("admin", "YourSecurePassword123!")
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)
instance.save()
new File("/var/lib/jenkins/admin-ready.txt").write("Admin user created at ${new Date()}")
new File("/var/lib/jenkins/init.groovy.d/01-admin.groovy").delete()
EOF

# Set permissions and start Jenkins
chown -R jenkins:jenkins /var/lib/jenkins/
systemctl enable jenkins && systemctl start jenkins

# Wait for Jenkins to be fully ready
echo "Waiting for Jenkins to be ready..."
for i in {1..120}; do
    if curl -s -f http://localhost:8080/login >/dev/null 2>&1; then
        echo "Jenkins is responding"
        break
    fi
    sleep 5
done

# Wait for admin user to be created
echo "Waiting for admin user setup..."
for i in {1..60}; do
    if [ -f "/var/lib/jenkins/admin-ready.txt" ]; then
        echo "Admin user is ready"
        break
    fi
    sleep 5
done

# Download Jenkins CLI
echo "Setting up Jenkins CLI..."
cd /var/lib/jenkins
wget -q http://localhost:8080/jnlpJars/jenkins-cli.jar

# Test CLI connection
echo "Testing Jenkins CLI connection..."
java -jar jenkins-cli.jar -s http://localhost:8080/ -auth admin:YourSecurePassword123! version

# Install plugins using CLI with dependency resolution
echo "Installing plugins via CLI..."
java -jar jenkins-cli.jar -s http://localhost:8080/ -auth admin:YourSecurePassword123! install-plugin \
    git \
    github \
    credentials \
    workflow-aggregator \
    build-timeout \
    timestamper \
    ws-cleanup \
    codedeploy \
    aws-codebuild \
    aws-credentials \
    aws-credentials \
    pipeline-aws
    

# Check installation status
echo "Checking plugin installation status..."
java -jar jenkins-cli.jar -s http://localhost:8080/ -auth admin:YourSecurePassword123! list-plugins > /var/log/plugins-installed.log

# Restart Jenkins to activate plugins
echo "Restarting Jenkins to activate plugins..."
java -jar jenkins-cli.jar -s http://localhost:8080/ -auth admin:YourSecurePassword123! restart

# Wait for restart to complete
echo "Waiting for Jenkins to restart..."
sleep 60

# Wait for Jenkins to be ready again
for i in {1..120}; do
    if curl -s -f http://localhost:8080/login >/dev/null 2>&1; then
        echo "Jenkins is ready after restart"
        break
    fi
    sleep 10
done

# Final verification
echo "Final plugin verification..."
java -jar jenkins-cli.jar -s http://localhost:8080/ -auth admin:YourSecurePassword123! list-plugins | grep -E "(git|github|workflow|codedeploy|aws-codebuild)" > /var/log/final-plugins.log

# Status report
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "localhost")
PLUGIN_COUNT=$(java -jar jenkins-cli.jar -s http://localhost:8080/ -auth admin:YourSecurePassword123! list-plugins | wc -l)

echo "=========================================="
echo "Jenkins setup completed: $(date)"
echo "URL: http://${PUBLIC_IP}:8080"
echo "Total plugins installed: $PLUGIN_COUNT"
echo "=========================================="

# Create completion marker
echo "Jenkins setup completed at $(date)" > /var/lib/jenkins/setup-complete.txt
