#!/bin/bash
set -e
source "./library.sh"

# Accept parameters 
NAT_NAME="$1"
NAT_AMI="$2"
NAT_TYPE="$3"
NAT_SG="$4"
PUBLIC_SUBNET_NAME="$5"
NAT_KEYPAIR="$6"

# VPC_NAME and VPC_ID are inherited from parent (exported by main.sh)
[[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; exit 1; }
[[ -z "$NAT_NAME" ]] && { print_error "NAT_NAME not provided"; exit 1; }
[[ -z "$NAT_AMI" ]] && { print_error "NAT_AMI not provided"; exit 1; }
[[ -z "$NAT_TYPE" ]] && { print_error "NAT_TYPE not provided"; exit 1; }
[[ -z "$NAT_SG" ]] && { print_error "NAT_SG not provided"; exit 1; }
[[ -z "$PUBLIC_SUBNET_NAME" ]] && { print_error "PUBLIC_SUBNET_NAME not provided"; exit 1; }
[[ -z "$NAT_KEYPAIR" ]] && { print_error "NAT_KEYPAIR not provided"; exit 1; }
[[ -z "$VPC_ID" ]] && { 
    print_warning "VPC_ID not set. Refreshing..."
    refresh_vpc_info
    [[ -z "$VPC_ID" ]] && { print_error "Failed to get VPC_ID"; exit 1; }
}

print_status "Configuring NAT Instance: $NAT_NAME"
print_status "Using VPC: Name=$VPC_NAME, ID=$VPC_ID"
print_status "Using keypair: $NAT_KEYPAIR"

# Get Subnet ID using inherited VPC_ID
SUBNET_ID=$(get_subnet_id_by_name "$PUBLIC_SUBNET_NAME")

# Get Security Group ID using inherited VPC_ID
SG_ID=$(get_sg_id_by_name "$NAT_SG")
[[ "$SG_ID" == "None" || -z "$SG_ID" ]] && { print_error "Security group '$NAT_SG' not found"; exit 1; }

# Check if NAT instance exists
INSTANCE_ID=$(get_instance_id_by_name "$NAT_NAME")

if [[ -n "$INSTANCE_ID" ]]; then
    print_status "NAT instance '$NAT_NAME' already exists with ID $INSTANCE_ID"
    
    INSTANCE_STATE=$(aws ec2 describe-instances \
        --instance-ids "$INSTANCE_ID" \
        --query "Reservations[0].Instances[0].State.Name" \
        --output text)
    
    print_status "Current state: $INSTANCE_STATE"
    
    # Ensure source/dest check is disabled
    CURRENT_SRC_DST=$(aws ec2 describe-instance-attribute \
        --instance-id "$INSTANCE_ID" \
        --attribute sourceDestCheck \
        --query "SourceDestCheck.Value" \
        --output text)
    
    if [[ "$CURRENT_SRC_DST" != "False" ]]; then
        print_status "Disabling source/destination check for NAT instance..."
        aws ec2 modify-instance-attribute \
            --instance-id "$INSTANCE_ID" \
            --no-source-dest-check
        print_status "Source/destination check disabled"
    else
        print_status "Source/destination check already disabled"
    fi
else
    print_status "Creating NAT instance '$NAT_NAME'..."
    
    # Create user data script for NAT configuration
    NAT_USER_DATA=$(cat << 'EOF'
#!/bin/bash
set -e

# Ensure SSH is enabled and running
systemctl enable ssh || systemctl enable sshd
systemctl start ssh || systemctl start sshd

# Enable IP forwarding
sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf

# Disable send_redirects
sysctl -w net.ipv4.conf.all.send_redirects=0
echo "net.ipv4.conf.all.send_redirects=0" >> /etc/sysctl.conf

# Determine the primary network interface (usually ens5)
PRIMARY_INTERFACE=$(ip route | grep default | awk '{print $5}')

# Configure iptables MASQUERADE for NAT
# MASQUERADE all outbound traffic EXCEPT traffic within the VPC (10.0.0.0/16)
iptables -t nat -A POSTROUTING -o $PRIMARY_INTERFACE ! -d 10.0.0.0/16 -j MASQUERADE

# Save iptables rules
mkdir -p /etc/iptables
iptables-save > /etc/iptables/rules.v4

# Enable iptables persistence service
systemctl enable netfilter-persistent
systemctl restart netfilter-persistent

# Create a startup script to restore iptables rules if needed
cat > /usr/local/bin/restore-nat-rules.sh << 'EOFSCRIPT'
#!/bin/bash
if [ -f /etc/iptables/rules.v4 ]; then
    iptables-restore < /etc/iptables/rules.v4
fi
EOFSCRIPT
chmod +x /usr/local/bin/restore-nat-rules.sh

echo "NAT configuration completed successfully"
EOF
)
    
    INSTANCE_ID=$(aws ec2 run-instances \
        --image-id "$NAT_AMI" \
        --instance-type "$NAT_TYPE" \
        --subnet-id "$SUBNET_ID" \
        --security-group-ids "$SG_ID" \
        --key-name "$NAT_KEYPAIR" \
        --associate-public-ip-address \
        --user-data "$NAT_USER_DATA" \
        --query "Instances[0].InstanceId" \
        --output text)
    
    print_status "Created NAT instance with ID $INSTANCE_ID"
    
    aws ec2 create-tags \
        --resources "$INSTANCE_ID" \
        --tags Key=Name,Value="$NAT_NAME"
    
    print_status "Waiting for NAT instance to be running..."
    aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
    print_status "NAT instance is now running"
    
    print_status "Waiting for NAT instance initialization (user data execution)..."
    sleep 10
    
    print_status "Disabling source/destination check for NAT instance..."
    aws ec2 modify-instance-attribute \
        --instance-id "$INSTANCE_ID" \
        --no-source-dest-check
    print_status "Source/destination check disabled"
fi

print_status "NAT instance setup complete: $INSTANCE_ID"
print_status "NAT Configuration:"
print_status "  - IP Forwarding: Enabled"
print_status "  - Send Redirects: Disabled"
print_status "  - iptables MASQUERADE: Configured"
print_status "  - Source/Dest Check: Disabled"
