#!/bin/bash
set -e
set -o pipefail
source "./library.sh"
trap 'print_error "Script failed at line $LINENO"; exit 1' ERR

mkdir -p logs
LOG_FILE="logs/cicd_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee "$LOG_FILE") 2>&1
ln -sf "$(basename "$LOG_FILE")" logs/cicd_latest.log


check_dependencies() {
    [[ ! -f "config.ini" ]] && { print_error "config.ini not found"; exit 1; }
    [[ ! -f "readconfig.py" ]] && { print_error "readconfig.py not found"; exit 1; }
    [[ ! -f "library.sh" ]] && { print_error "library.sh not found"; exit 1; }
    [[ ! -f "iam/iam_service.py" ]] && { print_error "iam/iam_service.py not found"; exit 1; }
    [[ ! -f "jenkins/main.py" ]] && { print_error "jenkins/main.py not found"; exit 1; }
    [[ ! -f "jenkins/jenkins_pipeline.py" ]] && { print_error "jenkins/jenkins_pipeline.py not found"; exit 1; }
    [[ ! -f "network/vpc.sh" ]] && { print_error "network/vpc.sh not found"; exit 1; }
    [[ ! -f "network/subnet.sh" ]] && { print_error "network/subnet.sh not found"; exit 1; }
    [[ ! -f "network/igw.sh" ]] && { print_error "network/igw.sh not found"; exit 1; }
    [[ ! -f "network/security_groups.sh" ]] && { print_error "network/security_groups.sh not found"; exit 1; }
    [[ ! -f "network/nat.sh" ]] && { print_error "network/nat.sh not found"; exit 1; }
    [[ ! -f "network/route_table.sh" ]] && { print_error "network/route_table.sh not found"; exit 1; }

    chmod +x network/vpc.sh network/subnet.sh network/igw.sh network/security_groups.sh network/nat.sh network/route_table.sh iam/iam_service.py jenkins/main.py jenkins/jenkins_pipeline.py
}


setup_network_jenkins() {
    code_deploy_infra=$1

    # Step 1: Create IAM resources first (independent of network)
    if python3 iam/iam_service.py \
        "$AWS_REGION" "$AWS_ACCOUNT_ID" "$S3_BUCKET_NAME" "$CONNECTION_ARN" \
        "$TOMCAT_ROLE" "$JENKINS_ROLE" "$CODEDEPLOY_INSTANCE_ROLE" \
        "$CODEDEPLOY_SERVICE_ROLE" "$CODEBUILD_SERVICE_ROLE" \
        "$TG1_ROLE" "$TG1_PROFILE" \
        "$PROJECT_NAME" "$ENVIRONMENT" "$MANAGED_BY"; then
        :
    else
        print_error "IAM automation failed! Check $LOG_FILE for details"
        exit 1
    fi

    # Step 2: Preload VPC info and export ALL variables for child processes
    # If VPC exists, VPC_ID is populated from AWS; if not, VPC_ID remains empty
    preload_vpc_info || true
    export VPC_NAME VPC_CIDR VPC_ID

    # Step 3: Create/validate network components
    # All child scripts inherit the exported variables
    ./network/vpc.sh
    ./network/subnet.sh "$SUBNET1_NAME" "$SUBNET1_CIDR" "$SUBNET1_AZ" \
                         "$SUBNET2_NAME" "$SUBNET2_CIDR" "$SUBNET2_AZ" \
                         "$SUBNET3_NAME" "$SUBNET3_CIDR" "$SUBNET3_AZ"
    ./network/igw.sh "$IGW_NAME"
    ./network/security_groups.sh "$NAT_SG"
    ./network/nat.sh "$NAT_NAME" "$NAT_AMI" "$NAT_TYPE" "$NAT_SG" "$SUBNET1_NAME" "$NAT_KEYPAIR"
    ./network/route_table.sh "$RT1_NAME" "$RT2_NAME" "$RT3_NAME" \
                             "$SUBNET1_NAME" "$SUBNET2_NAME" "$SUBNET3_NAME" \
                             "$IGW_NAME" "$NAT_NAME"

    # Step 4: Setup Jenkins infrastructure
    if python3 jenkins/main.py "$AWS_REGION" "$AWS_ACCOUNT_ID" "$code_deploy_infra"; then
        :
    else
        print_error "Jenkins infrastructure automation failed! Check $LOG_FILE for details"
        exit 1
    fi

    if python3 jenkins/jenkins_pipeline.py --non-interactive; then
        :
    else
        print_error "Jenkins pipeline configuration failed! Check $LOG_FILE for details"
        exit 1
    fi
}


main() {
    check_dependencies

    read AWS_REGION AWS_ACCOUNT_ID \
         S3_BUCKET_NAME CONNECTION_ARN \
         TOMCAT_ROLE JENKINS_ROLE CODEDEPLOY_INSTANCE_ROLE \
         CODEDEPLOY_SERVICE_ROLE CODEBUILD_SERVICE_ROLE \
         TG1_ROLE TG1_PROFILE \
         PROJECT_NAME ENVIRONMENT MANAGED_BY < <(python3 readconfig.py --multi \
        "AWS.region" "AWS.account_id" \
        "S3.bucket_name" "CodeConnections.connection_arn" \
        "IAM.tomcat_role" "IAM.jenkins_role" "IAM.codedeploy_instance_role" \
        "IAM.codedeploy_service_role" "IAM.codebuild_service_role" \
        "roles.tg1_role" "roles.tg1_profile" \
        "Tags.project_name" "Tags.environment_cicd" "Tags.managed_by" \
        | cut -d'=' -f2 | xargs)

    [[ -z "$AWS_REGION" ]] && { print_error "Failed to read AWS region from config"; exit 1; }
    [[ -z "$AWS_ACCOUNT_ID" ]] && { print_error "Failed to read AWS account ID from config"; exit 1; }
    [[ -z "$S3_BUCKET_NAME" ]] && { print_error "Failed to read S3 bucket name from config"; exit 1; }
    [[ -z "$CONNECTION_ARN" ]] && { print_error "Failed to read CodeConnections ARN from config"; exit 1; }
    [[ -z "$TOMCAT_ROLE" ]] && { print_error "Failed to read Tomcat role from config"; exit 1; }
    [[ -z "$JENKINS_ROLE" ]] && { print_error "Failed to read Jenkins role from config"; exit 1; }
    [[ -z "$PROJECT_NAME" ]] && { print_error "Failed to read project name from config"; exit 1; }
    [[ -z "$ENVIRONMENT" ]] && { print_error "Failed to read environment from config"; exit 1; }
    [[ -z "$MANAGED_BY" ]] && { print_error "Failed to read managed by from config"; exit 1; }

    read VPC_NAME VPC_CIDR \
         SUBNET1_NAME SUBNET1_CIDR SUBNET1_AZ \
         SUBNET2_NAME SUBNET2_CIDR SUBNET2_AZ \
         SUBNET3_NAME SUBNET3_CIDR SUBNET3_AZ \
         IGW_NAME \
         NAT_NAME NAT_AMI NAT_TYPE NAT_SG NAT_KEYPAIR \
         RT1_NAME RT2_NAME RT3_NAME < <(python3 readconfig.py --multi \
        "vpc.name" "vpc.CIDR" \
        "subnet.subnet1_name" "subnet.subnet1_cidr" "subnet.subnet1_az" \
        "subnet.subnet2_name" "subnet.subnet2_cidr" "subnet.subnet2_az" \
        "subnet.subnet3_name" "subnet.subnet3_cidr" "subnet.subnet3_az" \
        "igw.name" \
        "nat.name" "nat.ami" "nat.type" "nat.sg" "nat.keypair" \
        "rt.rt1_name" "rt.rt2_name" "rt.rt3_name" \
        | cut -d'=' -f2 | xargs)

    [[ -z "$VPC_NAME" ]] && { print_error "Failed to read VPC name from config"; exit 1; }
    [[ -z "$VPC_CIDR" ]] && { print_error "Failed to read VPC CIDR from config"; exit 1; }
    [[ -z "$SUBNET1_NAME" ]] && { print_error "Failed to read Subnet configuration"; exit 1; }
    [[ -z "$IGW_NAME" ]] && { print_error "Failed to read IGW configuration"; exit 1; }
    [[ -z "$NAT_NAME" ]] && { print_error "Failed to read NAT configuration"; exit 1; }
    [[ -z "$NAT_KEYPAIR" ]] && { print_error "Failed to read NAT keypair from config"; exit 1; }
    [[ -z "$RT1_NAME" ]] && { print_error "Failed to read Route Table configuration"; exit 1; }

    CODE_DEPLOY_INFRA=$(python3 readconfig.py CodeDeploy code_deploy_infra 2>/dev/null || echo "false")
    [[ -z "$CODE_DEPLOY_INFRA" ]] && CODE_DEPLOY_INFRA="false"
    print_status "Code Deploy Infra mode: $CODE_DEPLOY_INFRA"

    setup_network_jenkins "$CODE_DEPLOY_INFRA"

    find logs/ -name "cicd_*.log" -mtime +30 -delete 2>/dev/null || true
}


main "$@"

# End of script
