local k = import 'k.libsonnet';

local np = k.networking.v1.networkPolicy;
local egressRule = k.networking.v1.networkPolicyEgressRule;
local peer = k.networking.v1.networkPolicyPeer;
local portRule = k.networking.v1.networkPolicyPort;

local rfc1918 = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'];

local dnsRules = [
  egressRule.withPorts([
    portRule.withProtocol('UDP') + portRule.withPort(53),
    portRule.withProtocol('TCP') + portRule.withPort(53),
  ]),
];

local registryApiRule(port) = [
  egressRule.withPorts([portRule.withPort(port)])
  + egressRule.withTo([
    peer.podSelector.withMatchLabels({ app: 'registry-api' }),
  ]),
];

local externalRule(ports) = [
  egressRule.withPorts(std.map(function(p) portRule.withPort(p), ports))
  + egressRule.withTo([
    peer.ipBlock.withCidr('0.0.0.0/0')
    + peer.ipBlock.withExcept(rfc1918),
  ]),
];

{
  netpol_synthesis_egress:
    np.new('synthesis-egress')
    + np.metadata.withNamespace($._config.namespace)
    + np.spec.podSelector.withMatchLabels({ app: 'synthesis-service' })
    + np.spec.withPolicyTypes(['Egress'])
    + np.spec.withEgress(
      dnsRules
      + registryApiRule($._config.ports.registry_api)
      + externalRule([443])
    ),

  netpol_executor_egress:
    np.new('executor-egress')
    + np.metadata.withNamespace($._config.namespace)
    + np.spec.podSelector.withMatchLabels({ app: 'tool-executor' })
    + np.spec.withPolicyTypes(['Egress'])
    + np.spec.withEgress(
      dnsRules
      + registryApiRule($._config.ports.registry_api)
      + externalRule([80, 443])
    ),
}
