#!/usr/bin/env python
import base64
from constructs import Construct
from utils import ExtendedTerraformStack
from cdktf import Fn, TerraformOutput, Token
from imports.aws.data_aws_ami import DataAwsAmi, DataAwsAmiFilter
from imports.aws.ecs_cluster import EcsCluster
from imports.aws.ecs_account_setting_default import EcsAccountSettingDefault
from imports.aws.data_aws_iam_policy_document import DataAwsIamPolicyDocument, DataAwsIamPolicyDocumentStatement, DataAwsIamPolicyDocumentStatementPrincipals
from imports.aws.data_aws_iam_policy import DataAwsIamPolicy
from imports.aws.iam_role_policy_attachment import IamRolePolicyAttachment
from imports.aws.iam_role import IamRole
from imports.aws.iam_instance_profile import IamInstanceProfile
from imports.aws.launch_template import LaunchTemplate, LaunchTemplateIamInstanceProfile, LaunchTemplatePrivateDnsNameOptions
from imports.aws.autoscaling_group import AutoscalingGroup, AutoscalingGroupTag, AutoscalingGroupLaunchTemplate
from imports.aws.data_aws_instances import DataAwsInstances


class Ec2EcsClusterStack(ExtendedTerraformStack):
    def __init__(self, scope: Construct, ns: str, provider_config: dict, state_config: dict, cluster_config: dict):
        super().__init__(scope, ns, provider_config, state_config)

        # ECS settings
        self._init_cluster(cluster_config["cluster_name"])

        # IAM Roles
        ecs_instance_profile = self._init_IAM_roles()

        # Ec2 instance cluster
        self._init_autoscale_group(cluster_config, ecs_instance_profile)

        instances = DataAwsInstances(self, "Instances", instance_tags={"is_autoscale": "true"})

        # stack outputs
        TerraformOutput(self, "ami_id", value=self._ami.id)
        TerraformOutput(self, "public_ips", value=Token.as_list(instances.public_ips))

    def _init_autoscale_group(self, cluster_config, ecs_instance_profile):
        cluster_name = cluster_config["cluster_name"]
        self._ami = DataAwsAmi(self, "ami_ids",
                               most_recent=True,
                               owners=["amazon"],
                               filter=[DataAwsAmiFilter(name="name", values=["amzn2-ami-ecs-*"]),
                                       DataAwsAmiFilter(name="architecture", values=["arm64"])]
                               )

        # Launch Template
        template_profile = LaunchTemplateIamInstanceProfile(arn=ecs_instance_profile.arn)
        template_dns_options = LaunchTemplatePrivateDnsNameOptions(enable_resource_name_dns_a_record=False)
        user_data_str = f"""#!/bin/bash
                        cat <<EOF >> /etc/ecs/ecs.config
                        ECS_CLUSTER={cluster_name}
                        ECS_CONTAINER_INSTANCE_TAGS={{"name": "i-ecs-cluster-{cluster_name}"}}
                        EOF
                        """
        user_data_b64_bytes = base64.b64encode(user_data_str.encode('ascii'))

        template = LaunchTemplate(self, "LaunchTemplate",
                                  name=f"launch-template-{cluster_name}",
                                  image_id=self._ami.id,
                                  key_name=cluster_config["key_pair_name"],
                                  vpc_security_group_ids=cluster_config["security_groups"],
                                  iam_instance_profile=template_profile,
                                  private_dns_name_options=template_dns_options,
                                  user_data=user_data_b64_bytes.decode('ascii'),
                                  instance_type=cluster_config["instance_type"]
                                  )

        # Autoscaling Group
        self._as_group = AutoscalingGroup(self, "AutoscalingGroup",
                                    name=f"asg-ecs-cluster-{cluster_name}",
                                    vpc_zone_identifier=Token.as_list(cluster_config["vpc"].public_subnets_output),
                                    launch_template=AutoscalingGroupLaunchTemplate(id=template.id,
                                                                                   version="$Latest"
                                                                                   ),
                                    health_check_type="EC2",
                                    health_check_grace_period=300,
                                    desired_capacity=cluster_config["desired_capacity"],
                                    min_size=cluster_config["min_capacity"],
                                    max_size=cluster_config["max_capacity"],
                                    tag=[AutoscalingGroupTag(key="is_autoscale",
                                                             value="true",
                                                             propagate_at_launch=True)
                                         ]
                                    )

    def _init_IAM_roles(self):
        policy_ecs_for_ec2 = DataAwsIamPolicy(self, "EcsForEc2Policy", name="AmazonEC2ContainerServiceforEC2Role")

        policy_assume_role = DataAwsIamPolicyDocument(self, "PolicyDoc",
                                                      statement=[DataAwsIamPolicyDocumentStatement(
                                                          actions=["sts:AssumeRole"],
                                                          principals=[DataAwsIamPolicyDocumentStatementPrincipals(
                                                              type="Service",
                                                              identifiers=["ec2.amazonaws.com"]
                                                          )]
                                                      )])

        ecs_instance_role = IamRole(self, "EcsInstanceRole",
                                    name="role-ecs-instance",
                                    assume_role_policy=policy_assume_role.json)

        IamRolePolicyAttachment(self, "AttachPolicy",
                                role=ecs_instance_role.name,
                                policy_arn=policy_ecs_for_ec2.arn)

        ecs_instance_profile = IamInstanceProfile(self, "InstanceProfile",
                                                  name="role-profile-ecs-instance",
                                                  role=ecs_instance_role.name)

        return ecs_instance_profile

    def _init_cluster(self, cluster_name):
        EcsAccountSettingDefault(
            self, "AccountSettings", name="awsvpcTrunking", value="enabled")

        self._cluster = EcsCluster(self, "EcsCluster", name=cluster_name)

    @property
    def cluster(self):
        return self._cluster

    @property
    def autoscaling_group(self):
        return self._as_group
