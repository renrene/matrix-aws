#!/usr/bin/env python
from decouple import config
from cdktf import App, Fn, TerraformOutput, Token
from shared.vpc import VpcStack
from shared.ecs_cluster import Ec2EcsClusterStack
from shared.apigw import ApiGatewayStack
from data.rds_postgres import RdsPostgressDbStack
from apps.synapse import SynapseStack

# load env config
region = config('region', default='eu-west-1')
aws_profile = config('aws_profile', default='default')
tf_state_bucket = config('tf_state_bucket')
home_ip = config('home_ip')
key_pair_name = config('key_pair_name', default='my_key_pair')
apigw_custom_domain = config('apigw_custom_domain', default='example.com')
acm_cert_domain = config('acm_cert_domain', default='*.example.com')
private_namespace = config('private_namespace', default='matrix.lan')
ecs_instance_type = config('ecs_instance_type', default='a1.medium')


print("aws_profile: ", aws_profile)
print("bucket: ", tf_state_bucket)

#### global configs ####
provider_config = {
    "region": region,
    "profile": aws_profile
}

state_config = {
    "region": region,
    "profile": aws_profile,
    "bucket": tf_state_bucket
}


#### main app ####
app = App()

#### shared stacks ####
vpc_stack = VpcStack(app, "vpc", provider_config, state_config, home_ip, private_namespace, ecs_instance_type)

# API Gateway
apigw_stack = ApiGatewayStack(app, "apigw",
                              provider_config,
                              state_config,
                              api_config={
                                  "security_groups": [vpc_stack.vpc_sgroup.id],
                                  "subnets": Token.as_list(vpc_stack.vpc.public_subnets_output),
                                  "domain_name": apigw_custom_domain,
                                  "certificate_name": acm_cert_domain
                              })
apigw_stack.add_dependency(vpc_stack)

# ECS Cluster - EC2
ecs_cluster_stack = Ec2EcsClusterStack(app, "ecs-cluster",
                                       provider_config,
                                       state_config,
                                       cluster_config={
                                           "cluster_name": "shared",
                                           "key_pair_name": key_pair_name,
                                           "subnets_ids": [vpc_stack.primary_public_subnet_id],
                                           "security_groups": [vpc_stack.vpc_sgroup.id, vpc_stack.ssh_sgroup.id],
                                           "instance_type": ecs_instance_type,
                                           "desired_capacity": 1,
                                           "min_capacity": 1,
                                           "max_capacity": 1
                                       }
                                       )

ecs_cluster_stack.add_dependency(vpc_stack)

#### data stacks ####
db_config = {
    "db_name": "main-db",
    "vpc_id": vpc_stack.vpc.vpc_id_output,
    "preferred_az": vpc_stack.primary_availability_zone.name,
    "db_subnet_group_name": Token.as_string(vpc_stack.vpc.database_subnet_group_name_output),
    "sgroup_source_id": vpc_stack.vpc_sgroup.id,
    "engine_version": "13.6",
    "storage": 5,
    "max_storage": 10,

    "instance_class": "db.t4g.micro",
    "namespace_id": vpc_stack.namespace.id
}

rds_postrgres_db = RdsPostgressDbStack(app, "rds-postgres",
                                       provider_config,
                                       state_config,
                                       db_config,
                                       home_ip)

rds_postrgres_db.add_dependency(vpc_stack)

#### apps stacks ####
service_config = {
    "service_name": "synapse",
    "subnets_ids": [vpc_stack.primary_public_subnet_id],
    "sec_group_id": Token.as_string(vpc_stack.vpc_sgroup.id),
    "image": "matrixdotorg/synapse",
    "cpu": 128,
    "memory_soft": 128,
    "memory_hard": 1024,
    "env_vars": {
        "name": "varA",
        "value": "valueA"
    },
    "port_mappings": {
        "protocol": "tcp",
        "containerPort": 80,
        "hostPort": 80
    },
    "port": 80,
    "cluster_type": "EC2",
    "cluster_id": ecs_cluster_stack.cluster.id,
    "ns_id": vpc_stack.namespace.id,
    "api_gw_id": apigw_stack.api_id,
    "vpc_link_id": apigw_stack.vpc_link_id,
    "route_key": 'ANY /{proxy+}',
    "efs_id": vpc_stack.efs.id,
    "access_point_id": vpc_stack.efs_ap_synapse.id,
    "mount_path": "/data"
}

synapse_service = SynapseStack(app, "synapse-service",
                               provider_config,
                               state_config,
                               service_config)
synapse_service.add_dependency(ecs_cluster_stack)
synapse_service.add_dependency(rds_postrgres_db)

#### synth - end of code ####
app.synth()
