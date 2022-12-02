from constructs import Construct
from utils import ExtendedTerraformStack
from imports.aws.apigatewayv2_api import Apigatewayv2Api
from imports.aws.apigatewayv2_vpc_link import Apigatewayv2VpcLink
from imports.aws.apigatewayv2_stage import Apigatewayv2Stage
from imports.aws.apigatewayv2_domain_name import Apigatewayv2DomainName, Apigatewayv2DomainNameDomainNameConfiguration
from imports.aws.apigatewayv2_api_mapping import Apigatewayv2ApiMapping
from imports.aws.data_aws_acm_certificate import DataAwsAcmCertificate
from imports.aws.data_aws_route53_zone import DataAwsRoute53Zone
from imports.aws.route53_record import Route53Record, Route53RecordAlias


class ApiGatewayStack(ExtendedTerraformStack):
    def __init__(self, scope: Construct, ns: str, provider_config: dict, state_config: dict, api_config: dict):
        super().__init__(scope, ns, provider_config, state_config)

        self._api = Apigatewayv2Api(self, "APIGW",
                                    name="api-gw-shared",
                                    protocol_type="HTTP"
                                    )

        self._vpc_link = Apigatewayv2VpcLink(self, "VpcLink",
                                             name="vpc-link-shared",
                                             security_group_ids=api_config["security_groups"],
                                             subnet_ids=api_config["subnets"]
                                             )

        deafult_stage = Apigatewayv2Stage(self, "Stage",
                                          api_id=self._api.id,
                                          name="$default",
                                          auto_deploy=True
                                          )
        ssl_cert = DataAwsAcmCertificate(self, "main_cert", domain=api_config["certificate_name"])
        custom_domain = Apigatewayv2DomainName(self, "API_Domain",
                                               domain_name=api_config["domain_name"],
                                               domain_name_configuration=Apigatewayv2DomainNameDomainNameConfiguration(
                                                   endpoint_type="REGIONAL",
                                                   certificate_arn=ssl_cert.arn,
                                                   security_policy="TLS_1_2")
                                               )
        Apigatewayv2ApiMapping(self, "API_domain_mapping",
                               api_id=self._api.id,
                               stage=deafult_stage.name,
                               domain_name=custom_domain.domain_name)

        zone = DataAwsRoute53Zone(self, "hosted_zone", name=api_config["domain_name"])
        Route53Record(self, "DNS_Record_API",
                      zone_id=zone.id,
                      name=api_config["domain_name"],
                      type="A",
                      alias=[Route53RecordAlias(
                          zone_id=custom_domain.domain_name_configuration.hosted_zone_id,
                          name=custom_domain.domain_name_configuration.target_domain_name,
                          evaluate_target_health=True
                      )]
                      )

    @property
    def api_id(self):
        return self._api.id

    @property
    def vpc_link_id(self):
        return self._vpc_link.id
