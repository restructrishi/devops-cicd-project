#!/bin/bash

# library.sh - Shared functions and utilities
set -e

# Function to print messages
print_status() {
    echo "[INFO] $1" >&2
}

print_warning() {
    echo "[WARN] $1" >&2
}

print_error() {
    echo "[ERROR] $1" >&2
}

# GLOBAL VARIABLES - Exported by main.sh, inherited by child scripts
# VPC_NAME, VPC_CIDR, VPC_ID will be set and exported by main.sh
VPC_NAME="${VPC_NAME:-}"
VPC_CIDR="${VPC_CIDR:-}"
VPC_ID="${VPC_ID:-}"


# AWS LOOKUP FUNCTIONS

# Function to get IGW ID by Name
get_igw_id_by_name() {
    local NAME="$1"
    IGW_ID=$(aws ec2 describe-internet-gateways \
        --filters "Name=tag:Name,Values=$NAME" \
        --query "InternetGateways[0].InternetGatewayId" \
        --output text)

    [[ "$IGW_ID" == "None" ]] && IGW_ID=""
    echo "$IGW_ID"
}

# Get Subnet ID by Name tag 
get_subnet_id_by_name() {
    local SUBNET_NAME="$1"
    
    [[ -z "$VPC_ID" ]] && { print_error "VPC_ID not available"; exit 1; }
    
    SUBNET_ID=$(aws ec2 describe-subnets \
        --filters "Name=tag:Name,Values=$SUBNET_NAME" "Name=vpc-id,Values=$VPC_ID" \
        --query "Subnets[0].SubnetId" \
        --output text 2>/dev/null || echo "")
    if [[ -z "$SUBNET_ID" || "$SUBNET_ID" == "None" ]]; then
        echo "ERROR: Subnet with name '$SUBNET_NAME' not found in VPC $VPC_ID." >&2
        exit 1
    fi
    echo "$SUBNET_ID"
}

# Get EC2 instance by Name tag 
get_instance_id_by_name() {
    local INSTANCE_NAME="$1"
    INSTANCE_ID=$(aws ec2 describe-instances \
        --filters "Name=tag:Name,Values=$INSTANCE_NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
        --query "Reservations[0].Instances[0].InstanceId" \
        --output text 2>/dev/null || echo "")
    if [[ "$INSTANCE_ID" == "None" ]]; then
        INSTANCE_ID=""
    fi
    echo "$INSTANCE_ID"
}

# Get Security Group ID from name
get_sg_id_by_name() {
    local SG_NAME="$1"
    
    [[ -z "$VPC_ID" ]] && { print_error "VPC_ID not available"; exit 1; }
    
    aws ec2 describe-security-groups \
        --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
        --query "SecurityGroups[0].GroupId" --output text
}


# VPC INFORMATION FUNCTIONS

# Preload VPC information (ID and CIDR) with a single AWS API call
# Updates global VPC_ID and VPC_CIDR variables if VPC exists
# Returns: 0 if VPC found, 1 if not found
preload_vpc_info() {
    [[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; return 1; }
    
    # Check if already cached
    if [[ -n "$VPC_ID" ]]; then
        print_status "Using cached VPC info for: $VPC_NAME (ID=$VPC_ID, CIDR=$VPC_CIDR)"
        return 0
    fi
    
    print_status "Querying VPC information for: $VPC_NAME"
    
    # Single AWS API call - query VPC ID and actual CIDR from AWS
    local VPC_INFO=$(aws ec2 describe-vpcs \
        --filters "Name=tag:Name,Values=$VPC_NAME" \
        --query "Vpcs[0].[VpcId,CidrBlock]" \
        --output text 2>/dev/null || echo "None None")
    
    VPC_ID=$(echo "$VPC_INFO" | awk '{print $1}')
    
    # Validate VPC exists
    if [[ "$VPC_ID" == "None" || -z "$VPC_ID" ]]; then
        print_warning "VPC '$VPC_NAME' not found (will be created)"
        VPC_ID=""
        # VPC_CIDR remains as set from config.ini, not cleared
        return 1
    fi
    
    # VPC exists - update VPC_CIDR with actual value from AWS
    VPC_CIDR=$(echo "$VPC_INFO" | awk '{print $2}')
    
    print_status "Loaded VPC: Name=$VPC_NAME, ID=$VPC_ID, CIDR=$VPC_CIDR"
    return 0
}

# Refresh VPC info after creation (cache miss handler)
refresh_vpc_info() {
    print_status "Refreshing VPC information..."
    VPC_ID=""  # Clear cache to force reload
    preload_vpc_info
}