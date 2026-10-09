import json
from pathlib import Path
import subprocess
import unittest

from node_filter import is_subscription_placeholder, strip_subscription_placeholders
from sync import is_junk_or_auto


class SubscriptionPlaceholderTests(unittest.TestCase):
    def examples(self):
        junk = [
            '[Provider] ⏬ Если глушат интернет ⏬',
            '[Provider] Ниже для телефона', '[Provider] Выше для ПК',
            '[Provider] Для телефона', '[Provider] For mobile',
            '[Provider] Below for phone', '[Provider] 🌐 Балансировщик',
            '[Provider] Балансер Европы', '[Provider] Auto-Select',
            '[Provider] Load-Balance', '[Provider] ━━━━━',
            '[Provider] Остаток трафика 50 GB', '[Provider] Подписка истекает завтра',
        ]
        real = [
            '[Provider] 🇫🇮 LTE #1', '[Provider] 🇩🇪 Обход Резерв (только Wi-Fi)',
            '[Provider] 🇪🇺🏳️ Обход 2.1', '[Provider] 🇩🇪 Москва → Германия',
            '[Provider] 🇳🇱 Netherlands', '[Provider] 🇸🇪 Wi-Fi | Швеция 10гбит #1',
        ]
        return [(name, True) for name in junk] + [(name, False) for name in real]

    def test_headings_and_balancers_are_removed_but_real_nodes_remain(self):
        for name, expected in self.examples():
            with self.subTest(name=name):
                proxy = {'name': name, 'type': 'vless', 'server': '192.0.2.1', 'port': 443}
                self.assertEqual(is_subscription_placeholder(proxy), expected)
                self.assertEqual(is_junk_or_auto(proxy), expected)

    def test_group_references_are_cleaned_without_removing_routing_groups(self):
        config = {'proxies': [
            {'name': '[P] Ниже для телефона', 'type': 'vless'},
            {'name': '[P] Finland', 'type': 'vless'},
        ], 'proxy-groups': [{'name': 'Auto-Fallback', 'type': 'fallback',
                             'proxies': ['[P] Ниже для телефона', '[P] Finland']}]}
        self.assertEqual(strip_subscription_placeholders(config), {'[P] Ниже для телефона'})
        self.assertEqual(config['proxy-groups'][0]['proxies'], ['[P] Finland'])
        self.assertEqual(config['proxy-groups'][0]['name'], 'Auto-Fallback')

    def test_verge_and_python_recognize_the_same_entries(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/verge_routing.js'
        data = [{'name': name, 'type': 'vless'} for name, _ in self.examples()]
        data.append({'name': '[P] 🇫🇮 pool', 'type': 'url-test'})
        js = "const fs=require('fs'),vm=require('vm');const c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(c.isSubscriptionPlaceholder)));"
        result = subprocess.run(['node', '-e', js, str(script)], input=json.dumps(data),
                                text=True, encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [is_subscription_placeholder(p) for p in data])
