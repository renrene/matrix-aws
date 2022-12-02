from constructs import Construct
from utils import ExtendedTerraformStack
from imports.aws.security_group import SecurityGroup
from imports.aws.security_group_rule import SecurityGroupRule
from imports.aws.service_discovery_private_dns_namespace import ServiceDiscoveryPrivateDnsNamespace
from imports.aws.efs_file_system import EfsFileSystem
from imports.aws.efs_access_point import EfsAccessPoint, EfsAccessPointRootDirectory, EfsAccessPointRootDirectoryCreationInfo, EfsAccessPointPosixUser
from imports.vpc import Vpc


class VpcStack(ExtendedTerraformStack):
    def __init__(self, scope: Construct, ns: str,
                 provider_config: dict,
                 state_config: dict,
                 home_ip: str,
                 private_namespace: str):
        super().__init__(scope, ns, provider_config, state_config)

        # create base vpc and security groups
        self._initVPC(home_ip, private_namespace)

        # create Service Discovery DNS namespace
        self._initCloudMap(private_namespace)

        # create Shared File System
        self._intEFS()

    def _initVPC(self, home_ip: str, private_namespace: str):
        self._vpc = Vpc(self, "shared_vpc",
                        name="shared_vpc",
                        cidr="10.144.0.0/16",
                        azs=[f'{self._provider.region}b',
                             f'{self._provider.region}c'],
                        private_subnets=["10.144.5.0/24", "10.144.6.0/24"],
                        public_subnets=["10.144.0.0/24", "10.144.1.0/24"],
                        database_subnets=["10.144.10.0/24", "10.144.11.0/24"],
                        enable_dns_hostnames=True,
                        enable_dns_support=True,
                        enable_dhcp_options=True,
                        dhcp_options_domain_name=private_namespace
                        )

        self._vpc_sg = SecurityGroup(self, "vpc_sg",
                                     vpc_id=self._vpc.vpc_id_output,
                                     name="Shared VPC"
                                     )

        SecurityGroupRule(self, "VPC Shared",
                          description="Shared Access",
                          security_group_id=self._vpc_sg.id,
                          from_port=0,
                          to_port=0,
                          protocol="-1",
                          self_attribute=True,
                          type="ingress"
                          )

        SecurityGroupRule(self, "ssh access",
                          description="World Access",
                          security_group_id=self._vpc_sg.id,
                          from_port=0,
                          to_port=0,
                          protocol="-1",
                          cidr_blocks=["0.0.0.0/0"],
                          type="egress"
                          )

        self._ssh_sg = SecurityGroup(self, "ssh_sg",
                                     vpc_id=self._vpc.vpc_id_output,
                                     name="SSH Access"
                                     )
        SecurityGroupRule(self, "SSH Access",
                          description="SSH Access",
                          security_group_id=self._ssh_sg.id,
                          from_port=22,
                          to_port=22,
                          protocol="tcp",
                          cidr_blocks=[home_ip],
                          type="ingress"
                          )

    def _initCloudMap(self, private_namespace: str):
        self._namespace = ServiceDiscoveryPrivateDnsNamespace(self, "CloudMap_namespace",
                                                              name=private_namespace,
                                                              description="Namespace for all privatier services",
                                                              vpc=self._vpc.vpc_id_output
                                                              )

    def _intEFS(self):
        self._efs = EfsFileSystem(self, "EFS",
                                  availability_zone_name=f'{self._provider.region}b',
                                  creation_token="shared_efs",
                                  encrypted=True
                                  )
        self._efs_ap_synapse = EfsAccessPoint(self, "EFS_AP_Synapse",
                                              file_system_id=self._efs.id,
                                              root_directory=EfsAccessPointRootDirectory(
                                                  path="/synapse/data", creation_info=EfsAccessPointRootDirectoryCreationInfo(
                                                    owner_gid=991,
                                                    owner_uid=991,
                                                    permissions="0755")),
                                              posix_user=EfsAccessPointPosixUser(uid=991,gid=991),
                                              )

    @property
    def vpc(self):
        return self._vpc

    @property
    def vpc_sgroup(self):
        return self._vpc_sg

    @property
    def ssh_sgroup(self):
        return self._ssh_sg

    @property
    def namespace(self):
        return self._namespace

    @property
    def efs(self):
        return self._efs

    @property
    def efs_ap_synapse(self):
        return self._efs_ap_synapse
