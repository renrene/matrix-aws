#!/usr/bin/env python
import json
from typing import Sequence

from cdktf import S3Backend, TerraformStack, Token, Fn
from constructs import Construct

from imports.aws.cloudwatch_log_group import CloudwatchLogGroup
from imports.aws.data_aws_iam_policy_document import (
    DataAwsIamPolicyDocument, DataAwsIamPolicyDocumentStatement,
    DataAwsIamPolicyDocumentStatementPrincipals)
from imports.aws.ecs_service import (EcsService,
                                     EcsServiceNetworkConfiguration,
                                     EcsServiceServiceRegistries)
from imports.aws.ecs_task_definition import EcsTaskDefinition, EcsTaskDefinitionVolume, EcsTaskDefinitionVolumeEfsVolumeConfiguration
from imports.aws.iam_policy import IamPolicy
from imports.aws.iam_role import IamRole
from imports.aws.iam_role_policy_attachment import IamRolePolicyAttachment
from imports.aws.provider import AwsProvider
from imports.aws.service_discovery_service import (
    ServiceDiscoveryService, ServiceDiscoveryServiceDnsConfig,
    ServiceDiscoveryServiceDnsConfigDnsRecords,
    ServiceDiscoveryServiceHealthCheckCustomConfig)
from imports.aws.apigatewayv2_integration import Apigatewayv2Integration
from imports.aws.apigatewayv2_route import Apigatewayv2Route


class ExtendedTerraformStack(TerraformStack):
    def __init__(self, scope: Construct, ns: str, provider_config: dict, state_config: dict):
        super().__init__(scope, ns)

        self._provider = AwsProvider(
            self, ns,
            region=provider_config["region"],
            profile=provider_config["profile"]
        )

        self._backend = S3Backend(self, bucket=state_config["bucket"],
                                  key=f"{ns}/tfstate",
                                  profile=state_config["profile"],
                                  region=state_config["region"]
                                  )


class EcsServiceStack(ExtendedTerraformStack):
    def __init__(self, scope: Construct, ns: str,
                 provider_config: dict,
                 state_config: dict,
                 service_config: dict):
        super().__init__(scope, ns, provider_config, state_config)

        # init Service in Service discovery registry
        self._initServiceDiscovery(service_config)

        # cloudwatch logs
        log_group = CloudwatchLogGroup(self, f"LogGroup_{service_config['service_name']}",
                                       name=f"log-group-{service_config['service_name']}",
                                       retention_in_days=7)
        # init IAM Roles and Polices
        self._initIAMRoles(service_config["service_name"], log_group.arn)

        # init Ecs Service
        self._initEcsService(provider_config["region"],
                             service_config["subnets_ids"],
                             service_config["sec_group_id"],
                             f"log-group-{service_config['service_name']}",
                             service_config)

        # init GW route
        self._initGatewayRoute(service_config["service_name"],
                               service_config["api_gw_id"],
                               service_config["vpc_link_id"],
                               self._reg_srv.arn,
                               service_config["route_key"])

    def _initEcsService(self, region: str, subnets_ids: Sequence[str], sg_ecs_id: str, log_group_name: str, service_config: dict):
        service_name = service_config["service_name"]
        efs_volume_name = f"{service_name}-EfsVolume"
        mount_path = service_config["mount_path"]
        task = [{
            "name": service_name,
            "image": service_config["image"],
            "cpu": service_config["cpu"],
            "memory": service_config["memory_hard"],
            "memoryReservation": service_config["memory_soft"],
            "essential": True,
            "environment": [service_config["env_vars"]],
            "portMappings": [service_config["port_mappings"]],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": log_group_name,
                    "awslogs-region": region,
                    "awslogs-stream-prefix": f"{service_name}-logs"
                }
            },
            "mountPoints": [
                {
                    "sourceVolume": efs_volume_name,
                    "containerPath": mount_path
                }
            ]

        }]

        efs_volume = EcsTaskDefinitionVolume(name=efs_volume_name,                              efs_volume_configuration=EcsTaskDefinitionVolumeEfsVolumeConfiguration(
            file_system_id=service_config["efs_id"],
            transit_encryption="ENABLED",
            authorization_config={"access_point_id": service_config["access_point_id"],   "iam": "ENABLED"}

        ))

        task_tef = EcsTaskDefinition(self, "TaskDef",
                                     family=service_name,
                                     cpu=str(service_config["cpu"]),
                                     memory=str(service_config["memory_hard"]),
                                     requires_compatibilities=[service_config["cluster_type"]],
                                     execution_role_arn=self._role_task_execution.arn,
                                     task_role_arn=self._role_task.arn,
                                     container_definitions=json.dumps(task),
                                     volume=[efs_volume])

        self._ecs_service = EcsService(self, "EcsService",
                                       name=service_name,
                                       cluster=service_config["cluster_id"],
                                       task_definition=task_tef.arn,
                                       launch_type=service_config["cluster_type"],
                                       desired_count=1,
                                       service_registries=EcsServiceServiceRegistries(
                                           registry_arn=self._reg_srv.arn,
                                           container_name=service_name,
                                           container_port=service_config["port"])
                                       )

    def _initIAMRoles(self, service_name: str, log_group_arn: str):
        ### Task Execution Role - Create Polices ###
        # CloudWatch
        policy_doc_cloudwatch_logs = DataAwsIamPolicyDocument(self, "LogsPolicyDoc",
                                                              statement=[DataAwsIamPolicyDocumentStatement(
                                                                  sid="CreateCloudWatchLogStreamsAndPutLogEvents",
                                                                  actions=["logs:CreateLogStream",
                                                                           "logs:PutLogEvents"],
                                                                  effect="Allow",
                                                                  resources=[f"{log_group_arn}:*"]
                                                              )])
        policy_cloudwatch_logs = IamPolicy(self, "CloudWatchPolicy",
                                           name=f"policy-ecs-allow-logs-{service_name}",
                                           description="Allow ECS tasks to create log groups in CloudWatch and write to them",
                                           policy=policy_doc_cloudwatch_logs.json
                                           )
        # ECR - pull images
        policy_doc_ecr = DataAwsIamPolicyDocument(self, "EcrPolicyDoc",
                                                  statement=[DataAwsIamPolicyDocumentStatement(
                                                      sid="GetContainerImage",
                                                      actions=["ecr:GetAuthorizationToken",
                                                               "ecr:BatchCheckLayerAvailability",
                                                               "ecr:GetDownloadUrlForLayer",
                                                               "ecr:BatchGetImage"],
                                                      effect="Allow",
                                                      resources=["*"]
                                                  )])

        policy_ecr = IamPolicy(self, "EcrAccessPolicy",
                               name=f"policy-ecs-allow-ecr-{service_name}",
                               description="Allow ECS tasks to pull images from ECR",
                               policy=policy_doc_ecr.json
                               )

        # Create Task Exec Role
        policy_doc_assume_role = DataAwsIamPolicyDocument(self, "AssumeRolePolicyDoc",
                                                          statement=[DataAwsIamPolicyDocumentStatement(
                                                              actions=["sts:AssumeRole"],
                                                              principals=[DataAwsIamPolicyDocumentStatementPrincipals(
                                                                  type="Service",
                                                                  identifiers=["ecs-tasks.amazonaws.com"]
                                                              )]
                                                          )])

        self._role_task_execution = IamRole(self, "TaskExecRole",
                                            name=f"role-ecs-task-exec-{service_name}",
                                            assume_role_policy=policy_doc_assume_role.json)

        # Task Execution Role - Attach policies
        IamRolePolicyAttachment(self, "TaskExec_AttachPolicy_CloudWatch",
                                role=self._role_task_execution.name,
                                policy_arn=policy_cloudwatch_logs.arn
                                )
        IamRolePolicyAttachment(self, "TaskExec_AttachPolicy_ECR",
                                role=self._role_task_execution.name,
                                policy_arn=policy_ecr.arn
                                )

        ####  Task Role and Policies  ###
        self._role_task = IamRole(self, "TaskRole",
                                  name=f"role-ecs-task-{service_name}",
                                  assume_role_policy=policy_doc_assume_role.json)

        IamRolePolicyAttachment(self, "TaskRole_AttachPolicy",
                                role=self._role_task.name,
                                policy_arn=policy_cloudwatch_logs.arn
                                )

    def _initServiceDiscovery(self, service_config: dict):
        self._reg_srv = ServiceDiscoveryService(self, "ServiceDiscovery",
                                                name=service_config["service_name"],
                                                dns_config=ServiceDiscoveryServiceDnsConfig(
                                                    namespace_id=service_config["ns_id"],
                                                    dns_records=[
                                                        ServiceDiscoveryServiceDnsConfigDnsRecords(
                                                            ttl=15,
                                                            type="SRV")
                                                    ],
                                                    routing_policy="MULTIVALUE"
                                                ),
                                                health_check_custom_config=ServiceDiscoveryServiceHealthCheckCustomConfig(
                                                    failure_threshold=1)
                                                )

    def _initGatewayRoute(self, service_name: str, api_id: str, vpc_link_id: str, service_arn: str, route: str):
        integration = Apigatewayv2Integration(self, f"API_Integration_{service_name}",
                                              api_id=api_id,
                                              integration_uri=service_arn,
                                              integration_type="HTTP_PROXY",
                                              integration_method="ANY",
                                              connection_type="VPC_LINK",
                                              connection_id=vpc_link_id)
        Apigatewayv2Route(self, f"API_Route_{service_name}",
                          api_id=api_id,
                          route_key=route,
                          target=f"integrations/{integration.id}")

    @property
    def task_exec_role(self):
        return self._role_task_execution

    @property
    def task_role(self):
        return self._role_task

    @property
    def registry_service(self):
        return self._reg_srv

    # @property
    # def ecs_service(self):
    #     return self._ecs_service
