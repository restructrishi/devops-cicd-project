#!/bin/bash
set -e
source "./library.sh"

# Accept parameters
RT1_NAME="$1"
RT2_NAME="$2"
RT3_NAME="$3"
SUBNET1_NAME="$4"
SUBNET2_NAME="$5"
SUBNET3_NAME="$6"
IGW_NAME="$7"
NAT_INSTANCE_NAME="$8"

# VPC_NAME and VPC_ID are inherited from parent (exported by main.sh)
[[ -z "$VPC_NAME" ]] && { print_error "VPC_NAME not set"; exit 1; }
[[ -z "$RT1_NAME" ]] && { print_error "RT1_NAME not provided"; exit 1; }
[[ -z "$SUBNET1_NAME" ]] && { print_error "SUBNET1_NAME not provided"; exit 1; }
[[ -z "$IGW_NAME" ]] && { print_error "IGW_NAME not provided"; exit 1; }
[[ -z "$NAT_INSTANCE_NAME" ]] && { print_error "NAT_INSTANCE_NAME not provided"; exit 1; }
[[ -z "$VPC_ID" ]] && { 
    print_warning "VPC_ID not set. Refreshing..."
    refresh_vpc_info
    [[ -z "$VPC_ID" ]] && { print_error "Failed to get VPC_ID"; exit 1; }
}

print_status "Configuring Route Tables for VPC: Name=$VPC_NAME, ID=$VPC_ID"

# Helper functions
get_target_parameter() {
    local TARGET_TYPE="$1"
    local TARGET_ID="$2"
    [[ "$TARGET_TYPE" == "igw" ]] && echo "--gateway-id $TARGET_ID" || echo "--instance-id $TARGET_ID"
}

get_or_create_rt() {
    local RT_NAME="$1"

    RT_ID=$(aws ec2 describe-route-tables \
        --filters "Name=tag:Name,Values=$RT_NAME" "Name=vpc-id,Values=$VPC_ID" \
        --query "RouteTables[0].RouteTableId" \
        --output text)

    if [[ -z "$RT_ID" || "$RT_ID" == "None" ]]; then
        RT_ID=$(aws ec2 create-route-table \
            --vpc-id "$VPC_ID" \
            --query "RouteTable.RouteTableId" \
            --output text)
        aws ec2 create-tags \
            --resources "$RT_ID" \
            --tags Key=Name,Value="$RT_NAME"
        print_status "Created Route Table '$RT_NAME' with ID $RT_ID"
    else
        print_status "Route Table '$RT_NAME' already exists with ID $RT_ID"
    fi

    echo "$RT_ID"
}

associate_subnet_rt() {
    local RT_ID="$1"
    local SUBNET_ID="$2"

    ASSOCIATION_ID=$(aws ec2 describe-route-tables \
        --route-table-ids "$RT_ID" \
        --query "RouteTables[0].Associations[?SubnetId=='$SUBNET_ID'].RouteTableAssociationId | [0]" \
        --output text || echo "")

    if [[ -z "$ASSOCIATION_ID" || "$ASSOCIATION_ID" == "None" ]]; then
        aws ec2 associate-route-table \
            --route-table-id "$RT_ID" \
            --subnet-id "$SUBNET_ID"
        print_status "Associated Route Table $RT_ID with Subnet $SUBNET_ID"
    else
        print_status "Subnet $SUBNET_ID already associated with Route Table $RT_ID"
    fi
}

create_or_update_default_route() {
    local RT_ID="$1"
    local TARGET_TYPE="$2"
    local TARGET_ID="$3"

    ROUTE_INFO=$(aws ec2 describe-route-tables \
        --route-table-ids "$RT_ID" \
        --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].[GatewayId,InstanceId,State]" \
        --output text)

    EXISTING_GATEWAY=$(echo "$ROUTE_INFO" | awk '{print $1}')
    EXISTING_INSTANCE=$(echo "$ROUTE_INFO" | awk '{print $2}')
    STATE=$(echo "$ROUTE_INFO" | awk '{print $3}')

    if [[ "$STATE" == "blackhole" ]]; then
        print_status "Replacing blackhole default route in RT $RT_ID"
        aws ec2 replace-route \
            --route-table-id "$RT_ID" \
            --destination-cidr-block "0.0.0.0/0" \
            $(get_target_parameter "$TARGET_TYPE" "$TARGET_ID")
        return
    fi

    if [[ "$TARGET_TYPE" == "igw" && "$EXISTING_GATEWAY" == "$TARGET_ID" ]]; then
        print_status "Default route to IGW $TARGET_ID already exists in RT $RT_ID"
    elif [[ "$TARGET_TYPE" == "nat" && "$EXISTING_INSTANCE" == "$TARGET_ID" ]]; then
        print_status "Default route to NAT $TARGET_ID already exists in RT $RT_ID"
    else
        if [[ -n "$EXISTING_GATEWAY" || -n "$EXISTING_INSTANCE" ]]; then
            print_status "Updating default route in RT $RT_ID to $TARGET_TYPE $TARGET_ID"
            aws ec2 replace-route \
                --route-table-id "$RT_ID" \
                --destination-cidr-block "0.0.0.0/0" \
                $(get_target_parameter "$TARGET_TYPE" "$TARGET_ID")
        else
            print_status "Creating default route in RT $RT_ID to $TARGET_TYPE $TARGET_ID"
            aws ec2 create-route \
                --route-table-id "$RT_ID" \
                --destination-cidr-block "0.0.0.0/0" \
                $(get_target_parameter "$TARGET_TYPE" "$TARGET_ID")
        fi
    fi
}

# Main execution - Use inherited VPC_ID
SUBNET1_ID=$(get_subnet_id_by_name "$SUBNET1_NAME")
SUBNET2_ID=$(get_subnet_id_by_name "$SUBNET2_NAME")
SUBNET3_ID=$(get_subnet_id_by_name "$SUBNET3_NAME")

IGW_ID=$(get_igw_id_by_name "$IGW_NAME")
NAT_ID=$(get_instance_id_by_name "$NAT_INSTANCE_NAME")

[[ -z "$IGW_ID" ]] && { print_error "Internet Gateway '$IGW_NAME' not found"; exit 1; }
[[ -z "$NAT_ID" ]] && { print_error "NAT Instance '$NAT_INSTANCE_NAME' not found"; exit 1; }

RT1_ID=$(get_or_create_rt "$RT1_NAME")
RT2_ID=$(get_or_create_rt "$RT2_NAME")
RT3_ID=$(get_or_create_rt "$RT3_NAME")

associate_subnet_rt "$RT1_ID" "$SUBNET1_ID"
associate_subnet_rt "$RT2_ID" "$SUBNET2_ID"
associate_subnet_rt "$RT3_ID" "$SUBNET3_ID"

create_or_update_default_route "$RT1_ID" "igw" "$IGW_ID"
create_or_update_default_route "$RT2_ID" "nat" "$NAT_ID"
create_or_update_default_route "$RT3_ID" "igw" "$IGW_ID"

print_status "All route tables configured successfully"