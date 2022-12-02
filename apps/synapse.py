#!/usr/bin/env python
from constructs import Construct
from utils import EcsServiceStack
from cdktf import Fn, TerraformOutput, Token


class SynapseStack(EcsServiceStack):
    def __init__(self, scope: Construct, ns: str, provider_config: dict, state_config: dict, service_config: dict):
        super().__init__(scope, ns, provider_config, state_config, service_config)
