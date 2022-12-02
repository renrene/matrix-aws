#!/usr/bin/env python
from cdktf import App, Fn, TerraformOutput, Token
from shared.vpc import VpcStack
from shared.ecs_cluster import Ec2EcsClusterStack
from shared.apigw import ApiGatewayStack
from data.rds_postgres import RdsPostgressDbStack
from apps.synapse import SynapseStack

#### global configs ####
provider_config = {
    "region": "eu-west-1",
    "profile": "privatier"
}

state_config = {
    "region": "eu-west-1",
    "profile": "privatier",
    "bucket": "privatier-cdktf-remote-state"
}

home_ip = "87.68.196.67/32"

#### main app ####
app = App()

#### shared stacks ####
vpc_stack = VpcStack(app, "vpc", provider_config, state_config, home_ip, private_namespace="privatier.lan")

ecs_cluster_stack = Ec2EcsClusterStack(app, "ecs-cluster",
                                       provider_config,
                                       state_config,
                                       cluster_config={
                                           "cluster_name": "shared",
                                           "key_pair_name": "key-pair-main",
                                           "vpc": vpc_stack.vpc,
                                           "security_groups": [vpc_stack.vpc_sgroup.id, vpc_stack.ssh_sgroup.id],
                                           "instance_type": "a1.medium",
                                           "desired_capacity": 1,
                                           "min_capacity": 1,
                                           "max_capacity": 1
                                       }
                                       )

apigw_stack = ApiGatewayStack(app, "apigw",
                              provider_config,
                              state_config,
                              api_config={
                                  "security_groups": [vpc_stack.vpc_sgroup.id],
                                  "subnets": Token.as_list(vpc_stack.vpc.public_subnets_output),
                                  "domain_name": "net.rhizomatic.biz",
                                  "certificate_name": "*.net.rhizomatic.biz"
                              })

#### data stacks ####
db_config = {
    "db_name": "main-db",
    "vpc_id": vpc_stack.vpc.vpc_id_output,
    "sgroup_source_id": vpc_stack.vpc_sgroup.id,
    "engine_version": "13.6",
    "storage": 5,
    "max_storage": 10,
    "db_subnet_group_name": Token.as_string(vpc_stack.vpc.database_subnet_group_name_output),
    "instance_class": "db.t4g.micro",
    "namespace_id": vpc_stack.namespace.id
}

rds_postrgres_db = RdsPostgressDbStack(app, "rds-postgres",
                                       provider_config,
                                       state_config,
                                       db_config,
                                       home_ip)

#### apps stacks ####
service_config = {
    "service_name": "synapse",
    "subnets_ids": Token.as_list(vpc_stack.vpc.public_subnets_output),
    "sec_group_id": Token.as_string(vpc_stack.vpc_sgroup.id),
    "image": "public.ecr.aws/nginx/nginx:stable-alpine",
    "cpu": 128,
    "memory": 128,
    "env_vars": {
        "name": "varA",
        "value": "valueA"
    },
    "protocol": "tcp",
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

nginx_service = SynapseStack(app, "nginx-service",
                             provider_config,
                             state_config,
                             service_config)


#### synth - end of code ####
app.synth()
