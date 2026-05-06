#!/bin/bash
set -e
source "./library.sh"

# VPC_NAME and VPC_CIDR are inherited from parent (exported by main.sh)
[[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; exit 1; }
[[ -z "$VPC_CIDR" ]] && { print_error "VPC_CIDR not set"; exit 1; }

# Check if VPC exists using inherited VPC_ID
if [[ -n "$VPC_ID" ]]; then
    # VPC exists - validate CIDR matches expected value
    print_status "VPC '$VPC_NAME' exists with ID $VPC_ID"
    
    # Get current CIDR to validate
    CURRENT_CIDR=$(aws ec2 describe-vpcs \
        --vpc-ids "$VPC_ID" \
        --query "Vpcs[0].CidrBlock" \
        --output text)
    
    if [[ "$CURRENT_CIDR" != "$VPC_CIDR" ]]; then
        print_error "VPC '$VPC_NAME' CIDR mismatch! Expected: $VPC_CIDR, Found: $CURRENT_CIDR"
        exit 1
    fi
    
    print_status "VPC CIDR validated: $CURRENT_CIDR"
else
    # VPC doesn't exist - create it
    print_status "VPC '$VPC_NAME' does not exist. Creating..."
    
    VPC_ID=$(aws ec2 create-vpc \
        --cidr-block "$VPC_CIDR" \
        --query "Vpc.VpcId" \
        --output text)
    
    aws ec2 create-tags \
        --resources "$VPC_ID" \
        --tags Key=Name,Value="$VPC_NAME"
    
    # Enable DNS support and hostnames
    aws ec2 modify-vpc-attribute \
        --vpc-id "$VPC_ID" \
        --enable-dns-support
    
    aws ec2 modify-vpc-attribute \
        --vpc-id "$VPC_ID" \
        --enable-dns-hostnames
    
    print_status "Created VPC '$VPC_NAME' with ID $VPC_ID"
    
    # Update global VPC_ID for downstream scripts
    # Note: This updates the variable in current shell, but parent won't see it
    # Downstream scripts will call refresh_vpc_info() if they encounter a cache miss
    export VPC_ID
fi