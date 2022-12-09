#!/usr/bin/env python
from datetime import datetime
from cdktf import Fn, TerraformOutput, Token, TerraformResourceLifecycle
from constructs import Construct
from imports.aws.data_aws_db_snapshot import DataAwsDbSnapshot
from imports.aws.db_instance import DbInstance
from imports.aws.security_group import SecurityGroup
from imports.aws.security_group_rule import SecurityGroupRule
from imports.aws.service_discovery_instance import ServiceDiscoveryInstance
from imports.aws.service_discovery_service import ServiceDiscoveryService, ServiceDiscoveryServiceDnsConfig, ServiceDiscoveryServiceDnsConfigDnsRecords, ServiceDiscoveryServiceHealthCheckCustomConfig
from imports.aws.ssm_parameter import SsmParameter
from imports.random.provider import RandomProvider
from imports.random.password import Password
from utils import ExtendedTerraformStack


class RdsPostgressDbStack(ExtendedTerraformStack):
    def __init__(self, scope: Construct, ns: str,
                 provider_config: dict,
                 state_config: dict,
                 db_config: dict,
                 home_ip: str):
        super().__init__(scope, ns, provider_config, state_config)
        self._admin_username = "dbadmin"
        RandomProvider(self, "RandomProvider")

        # init random admin password and username as ssm params
        self._init_admin_credentials(db_config["db_name"])

        # handle network access
        self._init_security(db_config["vpc_id"], db_config["db_name"], db_config["sgroup_source_id"], home_ip)

        # create db instance
        db_instance_id = f"rds-postgres-{db_config['db_name']}"
        final_snapshot_str_time = datetime.isoformat(datetime.now()).replace(":", "-")[1:-7]
        snapshot = f"rds-snapshot-{db_config['db_name']}-{final_snapshot_str_time}"
        last_db_snapshot = DataAwsDbSnapshot(self, "Last_DB_Snapshot",
                                             most_recent=True,
                                             db_instance_identifier=db_instance_id)
        print("Last snapshot found: ", last_db_snapshot.id)
        self._db_instance = DbInstance(self, "DBInstance",
                                       identifier=db_instance_id,
                                       engine="postgres",
                                       engine_version=db_config["engine_version"],
                                       allocated_storage=db_config["storage"],
                                       max_allocated_storage=db_config["max_storage"],
                                       copy_tags_to_snapshot=True,
                                       db_subnet_group_name=db_config["db_subnet_group_name"],
                                       availability_zone=db_config["preferred_az"],
                                       username=self._admin_username,
                                       password=self._admin_pass.result,
                                       skip_final_snapshot=False,
                                       final_snapshot_identifier=snapshot,
                                       snapshot_identifier=last_db_snapshot.id,
                                       instance_class=db_config["instance_class"],
                                       vpc_security_group_ids=[self._db_sg.id],
                                       lifecycle=TerraformResourceLifecycle(
                                           ignore_changes=["final_snapshot_identifier", "snapshot_identifier"]
                                       )
                                       )

        self._initServiceDiscovery(db_config["namespace_id"], db_config["db_name"])

        TerraformOutput(self, "TerrafromOutput_DB_EndPoint", value=self._db_instance.endpoint)
        TerraformOutput(self, "TerrafromOutput_DB_SGroup", value=self._db_sg.id)

    def _init_security(self, vpc_id: str, db_name: str, sgroup_source_id: str, home_ip: str):
        self._db_sg = SecurityGroup(self, "DBSecurityGroup", name=f"sgroup-{db_name}", vpc_id=vpc_id)
        SecurityGroupRule(self, "DbAccessRule_Ingres",
                          description="Access to DB",
                          type="ingress",
                          security_group_id=self._db_sg.id,
                          protocol="tcp",
                          to_port=5432,
                          from_port=5432,
                          source_security_group_id=sgroup_source_id)

        SecurityGroupRule(self, "DbAccessRule_Home",
                          description="Access from Home",
                          type="ingress",
                          security_group_id=self._db_sg.id,
                          protocol="tcp",
                          to_port=5432,
                          from_port=5432,
                          cidr_blocks=[home_ip])

    def _init_admin_credentials(self, db_name: str):
        self._admin_pass = Password(self, "Password", length=16, override_special='!#$%&*()-_=+[]{}<>:?')
        SsmParameter(self, "Param_admin_user",
                     type="String",
                     name=f"/infra/rds-{db_name}/admin-username",
                     value=self._admin_username)
        SsmParameter(self, "Param_admin_pass",
                     type="SecureString",
                     name=f"/infra/rds-{db_name}/admin-password",
                     value=self._admin_pass.result)

    def _initServiceDiscovery(self, namespace_id: str, service_name: str):
        self._reg_srv = ServiceDiscoveryService(self, "ServiceDiscovery",
                                                name=service_name,
                                                dns_config=ServiceDiscoveryServiceDnsConfig(
                                                    namespace_id=namespace_id,
                                                    dns_records=[
                                                        ServiceDiscoveryServiceDnsConfigDnsRecords(
                                                            ttl=15,
                                                            type="CNAME")
                                                    ],
                                                    routing_policy="WEIGHTED"
                                                ),
                                                health_check_custom_config=ServiceDiscoveryServiceHealthCheckCustomConfig(
                                                    failure_threshold=1)
                                                )
        ServiceDiscoveryInstance(self, "ServiceDiscovery_DB",
                                 instance_id=self._db_instance.identifier,
                                 service_id=self._reg_srv.id,
                                 attributes={"AWS_INSTANCE_CNAME": self._db_instance.address})

    @property
    def db_security_group_id(self):
        return self._db_sg.id

    @property
    def db_instance(self):
        return self._db_instance
