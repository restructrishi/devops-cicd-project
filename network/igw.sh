#!/bin/bash
set -e
source "./library.sh"

# Accept parameters
IGW_NAME="$1"

# VPC_NAME and VPC_ID are inherited from parent (exported by main.sh)
[[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; exit 1; }
[[ -z "$IGW_NAME" ]] && { print_error "IGW_NAME not provided"; exit 1; }
[[ -z "$VPC_ID" ]] && { 
    print_warning "VPC_ID not set. Refreshing..."
    refresh_vpc_info
    [[ -z "$VPC_ID" ]] && { print_error "Failed to get VPC_ID"; exit 1; }
}

print_status "Using VPC: Name=$VPC_NAME, ID=$VPC_ID for IGW operations"

# Check if IGW exists
IGW_ID=$(get_igw_id_by_name "$IGW_NAME")

if [[ -n "$IGW_ID" ]]; then
    print_status "Internet Gateway '$IGW_NAME' exists with ID $IGW_ID"
else
    print_status "Internet Gateway '$IGW_NAME' does not exist. Creating..."
    IGW_ID=$(aws ec2 create-internet-gateway \
        --query "InternetGateway.InternetGatewayId" \
        --output text)
    
    aws ec2 create-tags \
        --resources "$IGW_ID" \
        --tags Key=Name,Value="$IGW_NAME"
    
    print_status "Created Internet Gateway '$IGW_NAME' with ID $IGW_ID"
fi

# Check if IGW is attached to the VPC
ATTACHED_VPC=$(aws ec2 describe-internet-gateways \
    --internet-gateway-ids "$IGW_ID" \
    --query "InternetGateways[0].Attachments[0].VpcId" \
    --output text)

if [[ "$ATTACHED_VPC" == "$VPC_ID" ]]; then
    print_status "Internet Gateway '$IGW_NAME' is already attached to VPC '$VPC_NAME'"
else
    print_status "Attaching Internet Gateway '$IGW_NAME' to VPC '$VPC_NAME'..."
    aws ec2 attach-internet-gateway \
        --internet-gateway-id "$IGW_ID" \
        --vpc-id "$VPC_ID"
    print_status "Attached IGW '$IGW_NAME' to VPC '$VPC_NAME'"
fi