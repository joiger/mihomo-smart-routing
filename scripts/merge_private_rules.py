"""Keep private routing overrides separate from the public generator."""
import yaml


def merge_private_rules(config_text, overrides_text):
    config = yaml.safe_load(config_text)
    overrides = yaml.safe_load(overrides_text) or {}
    rules = overrides.get('rules', [])
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        raise ValueError('Private rules must be a list of strings')
    targets = {'DIRECT', 'REJECT', 'REJECT-DROP'}
    targets.update(p['name'] for p in config.get('proxies', []))
    targets.update(g['name'] for g in config.get('proxy-groups', []))
    for rule in rules:
        parts = [p.strip() for p in rule.split(',')]
        target = parts[-2] if parts[-1] == 'no-resolve' else parts[-1]
        if len(parts) < 2 or target not in targets:
            raise ValueError('Private routing references an invalid policy')
    if not rules:
        return config_text
    config['rules'] = list(dict.fromkeys(rules + config.get('rules', [])))
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
