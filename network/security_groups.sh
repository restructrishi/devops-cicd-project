#!/bin/bash
set -euo pipefail
source "./library.sh"

# Accept Parameters
SG_NAT="$1"

# VPC_NAME, VPC_CIDR, VPC_ID are inherited from parent (exported by main.sh)
[[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; exit 1; }
[[ -z "$SG_NAT" ]] && { print_error "SG_NAT not provided"; exit 1; }
[[ -z "$VPC_ID" ]] && { 
    print_warning "VPC_ID not set. Refreshing..."
    refresh_vpc_info
    [[ -z "$VPC_ID" ]] && { print_error "Failed to get VPC_ID"; exit 1; }
}
[[ -z "$VPC_CIDR" ]] && { 
    print_warning "VPC_CIDR not set. Fetching..."
    VPC_CIDR=$(aws ec2 describe-vpcs \
        --vpc-ids "$VPC_ID" \
        --query "Vpcs[0].CidrBlock" \
        --output text)
    [[ -z "$VPC_CIDR" || "$VPC_CIDR" == "None" ]] && { print_error "Failed to get VPC_CIDR"; exit 1; }
}

print_status "Configuring Security Groups for VPC: Name=$VPC_NAME, ID=$VPC_ID, CIDR=$VPC_CIDR"

# Create NAT Security Group
SG_ID=$(get_sg_id_by_name "$SG_NAT")

if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
    print_status "Creating NAT security group: $SG_NAT"
    SG_ID=$(aws ec2 create-security-group \
        --group-name "$SG_NAT" \
        --description "Security group for NAT instance - allows all traffic" \
        --vpc-id "$VPC_ID" \
        --query "GroupId" \
        --output text)
    
    aws ec2 create-tags \
        --resources "$SG_ID" \
        --tags Key=Name,Value="$SG_NAT"
    
    print_status "Created NAT SG $SG_NAT with ID $SG_ID"
else
    print_status "NAT Security group $SG_NAT already exists with ID $SG_ID"
fi

# Configure inbound rules using VPC CIDR
EXISTING_RULE=$(aws ec2 describe-security-groups \
    --group-ids "$SG_ID" \
    --query "SecurityGroups[0].IpPermissions[?IpProtocol=='-1'].IpRanges[?CidrIp=='$VPC_CIDR']" \
    --output text 2>/dev/null || echo "")

if [[ -z "$EXISTING_RULE" ]]; then
    print_status "Adding inbound rule: All traffic from VPC CIDR ($VPC_CIDR)"
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --ip-permissions "[{\"IpProtocol\": \"-1\", \"IpRanges\": [{\"CidrIp\": \"$VPC_CIDR\"}]}]" 2>/dev/null || print_warning "Rule may already exist"
    print_status "NAT inbound rule configured"
else
    print_status "NAT inbound rule already exists"
fi

# Configure outbound rules
EXISTING_EGRESS=$(aws ec2 describe-security-groups \
    --group-ids "$SG_ID" \
    --query "SecurityGroups[0].IpPermissionsEgress[?IpProtocol=='-1'].IpRanges[?CidrIp=='0.0.0.0/0']" \
    --output text 2>/dev/null || echo "")

if [[ -z "$EXISTING_EGRESS" ]]; then
    print_status "Configuring NAT outbound rules (allow all)..."
    aws ec2 authorize-security-group-egress \
        --group-id "$SG_ID" \
        --ip-permissions '[{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]' 2>/dev/null || print_warning "Rule may already exist"
    print_status "NAT outbound rules configured"
else
    print_status "NAT outbound rules already configured"
fi

print_status "Security Groups setup complete"
