import copy
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from routing_policy import optimize_routing, primary_pool
from telemetry_db import init_db, record_probe, recalculate_scores, get_db_connection


class RoutingPolicyTests(unittest.TestCase):
    def config(self):
        names = ['[A] fast', '[A] second', '[B] backup', '[C] relay'] + ['[A] node' + str(i) for i in range(6)]
        return {'proxy-groups': [
            {'name': n, 'type': 'fallback', 'proxies': list(names), 'url': 'https://www.gstatic.com/generate_204'}
            for n in ['Auto-Fallback', '🛡️ Mobile-Bypass', '🤖 Auto-AI-Stable', 'Auto-UrlTest']],
            'rules': ['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
                      'DOMAIN,daily-cloudcode-pa.googleapis.com,🤖 AI-Services', 'MATCH,PROXY']}

    def test_diverse_pool_honors_first_preference_and_ignores_missing_nodes(self):
        pool = primary_pool(['[A] one', '[A] two', '[B] three', '[C] four'], 3,
                            ['gone', '[A] two'])
        self.assertEqual(pool, ['[A] two', '[B] three', '[C] four'])

    def test_reserve_retains_every_node_without_cycles(self):
        cfg = self.config()
        original = set(cfg['proxy-groups'][0]['proxies'])
        optimize_routing(cfg)
        groups = {g['name']: g for g in cfg['proxy-groups']}
        for name, reserve in [('Auto-Fallback', 'Aegis-Reserve'),
                              ('🛡️ Mobile-Bypass', 'Aegis-Mobile-Reserve'),
                              ('🤖 Auto-AI-Stable', 'Aegis-AI-Reserve')]:
            primary = groups[name]['proxies']
            self.assertEqual(len(primary), 6)
            self.assertEqual(set(primary[:-1]) | set(groups[reserve]['proxies']), original)
            self.assertNotIn(name, groups[reserve]['proxies'])
            self.assertTrue(groups[reserve]['lazy'])

    def test_reapply_is_idempotent_and_preserves_loopback_precedence(self):
        cfg = optimize_routing(self.config())
        before = copy.deepcopy(cfg)
        optimize_routing(cfg)
        self.assertEqual(cfg, before)
        self.assertTrue(cfg['rules'][0].startswith('IP-CIDR,127.'))
        self.assertLess(cfg['rules'].index('DOMAIN,daily-cloudcode-pa.googleapis.com,Aegis-Antigravity'),
                        cfg['rules'].index('DOMAIN,daily-cloudcode-pa.googleapis.com,🤖 AI-Services'))

    def test_verge_overlay_matches_generator(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/verge_routing.js'
        js = "const fs=require('fs'),vm=require('vm');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify(c.main(JSON.parse(fs.readFileSync(0,'utf8')))));"
        result = subprocess.run(['node', '-e', js, str(script)], input=json.dumps(self.config()),
                                text=True, encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), optimize_routing(self.config()))

    def test_block_expires_and_is_service_specific(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / 'telemetry.db')
            init_db(db)
            now = int(time.time())
            record_probe('node', 50, category='claude', is_geo_blocked=1, timestamp=now - 1200, db_path=db)
            record_probe('node', 50, category='openai', is_geo_blocked=1, timestamp=now, db_path=db)
            record_probe('node', 50, category='general', timestamp=now, db_path=db)
            recalculate_scores(db)
            conn = get_db_connection(db)
            rows = dict(conn.execute('SELECT category, calculated_score FROM node_scores'))
            conn.close()
            self.assertLess(rows['claude'], 100000)
            self.assertGreater(rows['openai'], 100000)
            self.assertLess(rows['general'], 100000)

    def test_later_success_clears_block_but_timeout_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / 'telemetry.db')
            init_db(db)
            now = int(time.time())
            record_probe('node', 50, category='ai', is_geo_blocked=1, timestamp=now - 2, db_path=db)
            record_probe('node', -1, category='ai', timestamp=now - 1, db_path=db)
            recalculate_scores(db)
            conn = get_db_connection(db)
            self.assertGreater(conn.execute('SELECT calculated_score FROM node_scores').fetchone()[0], 100000)
            conn.close()
            record_probe('node', 50, category='ai', timestamp=now, db_path=db)
            recalculate_scores(db)
            conn = get_db_connection(db)
            self.assertLess(conn.execute('SELECT calculated_score FROM node_scores').fetchone()[0], 100000)
            conn.close()

    def test_ai_max_trust_extracts_us_nodes_and_syncs_with_verge(self):
        cfg = {
            'proxy-groups': [
                {'name': '🤖 Auto-AI-Stable', 'type': 'fallback',
                 'proxies': ['[A] 🇩🇪 Germany', '[B] 🇺🇸 USA New York', '[C] 🇫🇮 Finland', '[D] 🇺🇸 USA Miami'],
                 'url': 'https://generativelanguage.googleapis.com'}
            ],
            'rules': ['MATCH,PROXY']
        }
        options = {'ai_max_trust_nodes': ['[D] 🇺🇸 USA Miami']}
        optimized = optimize_routing(cfg, options)
        mt = next(g for g in optimized['proxy-groups'] if g['name'] == '🤖 AI-Max-Trust')
        self.assertEqual(mt['type'], 'select')
        self.assertEqual(mt['proxies'], ['[D] 🇺🇸 USA Miami', '[B] 🇺🇸 USA New York'])

        # Parity with verge script
        script = Path(__file__).resolve().parents[1] / 'scripts/verge_routing.js'
        js = "const fs=require('fs'),vm=require('vm');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);c.OPTIONS.ai_max_trust_nodes=['[D] 🇺🇸 USA Miami'];console.log(JSON.stringify(c.main(JSON.parse(fs.readFileSync(0,'utf8')))));"
        result = subprocess.run(['node', '-e', js, str(script)], input=json.dumps({
            'proxy-groups': [
                {'name': '🤖 Auto-AI-Stable', 'type': 'fallback',
                 'proxies': ['[A] 🇩🇪 Germany', '[B] 🇺🇸 USA New York', '[C] 🇫🇮 Finland', '[D] 🇺🇸 USA Miami'],
                 'url': 'https://generativelanguage.googleapis.com'}
            ],
            'rules': ['MATCH,PROXY']
        }), text=True, encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), optimized)

    def test_ai_killswitch_replaces_direct_with_reject_and_syncs_with_verge(self):
        cfg = {
            'proxy-groups': [
                {'name': '🤖 AI-Services', 'type': 'select',
                 'proxies': ['Auto-Fallback', 'DIRECT']},
                {'name': 'Auto-Fallback', 'type': 'fallback',
                 'proxies': ['[A] 🇺🇸 USA Node', '[B] 🇩🇪 DE Node'],
                 'url': 'https://www.gstatic.com/generate_204'}
            ],
            'rules': ['MATCH,PROXY']
        }
        optimized = optimize_routing(cfg)
        ai_srv = next(g for g in optimized['proxy-groups'] if g['name'] == '🤖 AI-Services')
        self.assertNotIn('DIRECT', ai_srv['proxies'])
        self.assertIn('REJECT', ai_srv['proxies'])
        self.assertEqual(ai_srv['proxies'], ['Auto-Fallback', 'REJECT'])

        # Parity with verge script
        script = Path(__file__).resolve().parents[1] / 'scripts/verge_routing.js'
        js = "const fs=require('fs'),vm=require('vm');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify(c.main(JSON.parse(fs.readFileSync(0,'utf8')))));"
        test_input = {
            'proxy-groups': [
                {'name': '🤖 AI-Services', 'type': 'select',
                 'proxies': ['Auto-Fallback', 'DIRECT']},
                {'name': 'Auto-Fallback', 'type': 'fallback',
                 'proxies': ['[A] 🇺🇸 USA Node', '[B] 🇩🇪 DE Node'],
                 'url': 'https://www.gstatic.com/generate_204'}
            ],
            'rules': ['MATCH,PROXY']
        }
        result = subprocess.run(['node', '-e', js, str(script)], input=json.dumps(test_input),
                                text=True, encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), optimized)

    def test_ai_dns_policy_and_subservices_no_direct(self):
        cfg = {
            'proxy-groups': [
                {'name': '🤖 Auto-AI-Stable', 'type': 'fallback',
                 'proxies': ['[A] 🇺🇸 USA Node', '[B] 🇩🇪 DE Node'],
                 'url': 'https://generativelanguage.googleapis.com'},
                {'name': '🤖 AI-Services', 'type': 'select',
                 'proxies': ['🤖 AI-Max-Trust', 'DIRECT']}
            ],
            'dns': {
                'nameserver-policy': {
                    '+.ru': '77.88.8.8'
                }
            },
            'rules': ['MATCH,PROXY']
        }
        optimized = optimize_routing(cfg)

        # AI-Services killswitch check
        ai_srv = next(g for g in optimized['proxy-groups'] if g['name'] == '🤖 AI-Services')
        self.assertNotIn('DIRECT', ai_srv['proxies'])
        self.assertIn('REJECT', ai_srv['proxies'])

        # DNS isolation check
        ns_policy = optimized['dns']['nameserver-policy']
        for domain in ['+.openai.com', '+.anthropic.com', '+.cloudcode-pa.googleapis.com', '+.deepseek.com']:
            self.assertEqual(ns_policy.get(domain), 'https://dns.google/dns-query#🤖 AI-Max-Trust')

        # Sub-service groups check
        for service in ['Aegis-Antigravity', 'Aegis-Google-AI', 'Aegis-Claude', 'Aegis-OpenAI']:
            grp = next((g for g in optimized['proxy-groups'] if g['name'] == service), None)
            self.assertIsNotNone(grp)
            self.assertNotIn('DIRECT', grp['proxies'])

        # Parity with verge script
        script = Path(__file__).resolve().parents[1] / 'scripts/verge_routing.js'
        js = "const fs=require('fs'),vm=require('vm');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify(c.main(JSON.parse(fs.readFileSync(0,'utf8')))));"
        test_input = {
            'proxy-groups': [
                {'name': '🤖 Auto-AI-Stable', 'type': 'fallback',
                 'proxies': ['[A] 🇺🇸 USA Node', '[B] 🇩🇪 DE Node'],
                 'url': 'https://generativelanguage.googleapis.com'},
                {'name': '🤖 AI-Services', 'type': 'select',
                 'proxies': ['🤖 AI-Max-Trust', 'DIRECT']}
            ],
            'dns': {
                'nameserver-policy': {
                    '+.ru': '77.88.8.8'
                }
            },
            'rules': ['MATCH,PROXY']
        }
        result = subprocess.run(['node', '-e', js, str(script)], input=json.dumps(test_input),
                                text=True, encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), optimized)

    def test_ai_rules_leak_to_direct_intercepted_and_converted_to_reject(self):
        cfg = {
            'proxy-groups': [
                {'name': '🤖 Auto-AI-Stable', 'type': 'fallback',
                 'proxies': ['[A] 🇺🇸 USA Node', '[B] 🇩🇪 DE Node'],
                 'url': 'https://generativelanguage.googleapis.com'},
                {'name': '🤖 AI-Services', 'type': 'select',
                 'proxies': ['🤖 AI-Max-Trust', 'REJECT']}
            ],
            'rules': [
                'DOMAIN-SUFFIX,openai.com,DIRECT',
                'DOMAIN,api.anthropic.com,DIRECT',
                'DOMAIN-SUFFIX,openrouter.ai,DIRECT',
                'DOMAIN-SUFFIX,ru,DIRECT',
                'MATCH,PROXY'
            ]
        }
        optimized = optimize_routing(cfg)
        rules = optimized['rules']
        # AI rules originally pointing to DIRECT must be converted to REJECT (killswitch)
        self.assertIn('DOMAIN-SUFFIX,openai.com,REJECT', rules)
        self.assertNotIn('DOMAIN-SUFFIX,openai.com,DIRECT', rules)
        self.assertIn('DOMAIN,api.anthropic.com,REJECT', rules)
        self.assertNotIn('DOMAIN,api.anthropic.com,DIRECT', rules)
        self.assertIn('DOMAIN-SUFFIX,openrouter.ai,REJECT', rules)
        self.assertNotIn('DOMAIN-SUFFIX,openrouter.ai,DIRECT', rules)
        # Non-AI direct rule must be preserved
        self.assertIn('DOMAIN-SUFFIX,ru,DIRECT', rules)

        # Parity with verge script
        script = Path(__file__).resolve().parents[1] / 'scripts/verge_routing.js'
        js = "const fs=require('fs'),vm=require('vm');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify(c.main(JSON.parse(fs.readFileSync(0,'utf8')))));"
        test_input = {
            'proxy-groups': [
                {'name': '🤖 Auto-AI-Stable', 'type': 'fallback',
                 'proxies': ['[A] 🇺🇸 USA Node', '[B] 🇩🇪 DE Node'],
                 'url': 'https://generativelanguage.googleapis.com'},
                {'name': '🤖 AI-Services', 'type': 'select',
                 'proxies': ['🤖 AI-Max-Trust', 'REJECT']}
            ],
            'rules': [
                'DOMAIN-SUFFIX,openai.com,DIRECT',
                'DOMAIN,api.anthropic.com,DIRECT',
                'DOMAIN-SUFFIX,openrouter.ai,DIRECT',
                'DOMAIN-SUFFIX,ru,DIRECT',
                'MATCH,PROXY'
            ]
        }
        result = subprocess.run(['node', '-e', js, str(script)], input=json.dumps(test_input),
                                text=True, encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), optimized)


