import unittest
import yaml
from scripts.merge_private_rules import merge_private_rules


class PrivateRulesTests(unittest.TestCase):
    config = 'proxies: []\nproxy-groups:\n- name: VPN\n  type: select\n  proxies: [DIRECT]\nrules: ["DOMAIN-SUFFIX,ru,DIRECT", "MATCH,VPN"]\n'
    overrides = 'rules: ["DOMAIN,ha.example.com,VPN"]\n'

    def test_override_precedes_regional_direct(self):
        result = yaml.safe_load(merge_private_rules(self.config, self.overrides))
        self.assertEqual(result['rules'][0], 'DOMAIN,ha.example.com,VPN')
        self.assertEqual(result['proxy-groups'], yaml.safe_load(self.config)['proxy-groups'])

    def test_idempotent(self):
        first = merge_private_rules(self.config, self.overrides)
        self.assertEqual(first, merge_private_rules(first, self.overrides))

    def test_no_overrides_keeps_original(self):
        self.assertEqual(self.config, merge_private_rules(self.config, ''))

    def test_missing_policy_aborts(self):
        with self.assertRaises(ValueError):
            merge_private_rules(self.config, 'rules: ["DOMAIN,ha.example.com,MISSING"]')

    def test_build_mihomo_config_includes_private_rules(self):
        import sync
        cfg = sync.build_mihomo_config([], user_options={"private_rules": ["DOMAIN,test.example.com,DIRECT"]})
        self.assertEqual(cfg["rules"][0], "DOMAIN,test.example.com,DIRECT")

