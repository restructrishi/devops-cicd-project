#!/bin/bash
set -e
source "./library.sh"

# Accept parameters
SUBNET1_NAME="$1"
SUBNET1_CIDR="$2"
SUBNET1_AZ="$3"
SUBNET2_NAME="$4"
SUBNET2_CIDR="$5"
SUBNET2_AZ="$6"
SUBNET3_NAME="$7"
SUBNET3_CIDR="$8"
SUBNET3_AZ="$9"

# VPC_NAME, VPC_CIDR, VPC_ID are inherited from parent (exported by main.sh)
[[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; exit 1; }
[[ -z "$VPC_ID" ]] && { 
    print_warning "VPC_ID not set. VPC may have just been created. Refreshing..."
    refresh_vpc_info
    [[ -z "$VPC_ID" ]] && { print_error "Failed to get VPC_ID after refresh"; exit 1; }
}

print_status "Using VPC: Name=$VPC_NAME, ID=$VPC_ID for subnet operations"

check_or_create_subnet() {
    local NAME="$1"
    local EXPECTED_CIDR="$2"
    local AZ="$3"
    local IS_PUBLIC="${4:-false}"
    
    SUBNET_INFO=$(aws ec2 describe-subnets \
        --filters "Name=tag:Name,Values=$NAME" "Name=vpc-id,Values=$VPC_ID" \
        --query "Subnets[0].[SubnetId,CidrBlock,AvailabilityZone]" \
        --output text || echo "None None None")
    
    SUBNET_ID=$(echo "$SUBNET_INFO" | awk '{print $1}')
    CIDR_FOUND=$(echo "$SUBNET_INFO" | awk '{print $2}')
    AZ_FOUND=$(echo "$SUBNET_INFO" | awk '{print $3}')
    
    if [[ "$SUBNET_ID" != "None" ]]; then
        print_status "Subnet '$NAME' exists with ID $SUBNET_ID"
        
        if [[ "$CIDR_FOUND" != "$EXPECTED_CIDR" ]]; then
            print_error "Subnet '$NAME' CIDR mismatch! Expected $EXPECTED_CIDR, found $CIDR_FOUND"
            exit 1
        fi
        
        if [[ "$AZ_FOUND" != "$AZ" ]]; then
            print_error "Subnet '$NAME' AZ mismatch! Expected $AZ, found $AZ_FOUND"
            exit 1
        fi
        
        print_status "CIDR and AZ match for subnet '$NAME': $CIDR_FOUND, $AZ_FOUND"
    else
        print_status "Subnet '$NAME' does not exist. Creating in AZ $AZ..."
        SUBNET_ID=$(aws ec2 create-subnet \
            --vpc-id "$VPC_ID" \
            --cidr-block "$EXPECTED_CIDR" \
            --availability-zone "$AZ" \
            --query "Subnet.SubnetId" \
            --output text)
        
        aws ec2 create-tags \
            --resources "$SUBNET_ID" \
            --tags Key=Name,Value="$NAME"
        
        print_status "Created subnet '$NAME' with ID $SUBNET_ID in AZ $AZ"
    fi
    
    if [[ "$IS_PUBLIC" == "true" ]]; then
        AUTO_ASSIGN=$(aws ec2 describe-subnets \
            --subnet-ids "$SUBNET_ID" \
            --query "Subnets[0].MapPublicIpOnLaunch" \
            --output text)
        
        if [[ "$AUTO_ASSIGN" != "True" ]]; then
            print_status "Enabling auto-assign public IP for subnet '$NAME'..."
            aws ec2 modify-subnet-attribute \
                --subnet-id "$SUBNET_ID" \
                --map-public-ip-on-launch
            print_status "Auto-assign public IP enabled for subnet '$NAME'"
        else
            print_status "Auto-assign public IP already enabled for subnet '$NAME'"
        fi
    fi
}

# Create subnets using inherited VPC_ID
check_or_create_subnet "$SUBNET1_NAME" "$SUBNET1_CIDR" "$SUBNET1_AZ" "true"
check_or_create_subnet "$SUBNET2_NAME" "$SUBNET2_CIDR" "$SUBNET2_AZ" "false"
check_or_create_subnet "$SUBNET3_NAME" "$SUBNET3_CIDR" "$SUBNET3_AZ" "false"

print_status "All three subnets configured successfully"