"""Identify subscription UI entries that are not individual proxy nodes."""
import re


def is_subscription_placeholder(proxy):
    if 'server' in proxy and str(proxy['server']).strip().lower() in {'', '127.0.0.1', '0.0.0.0', 'localhost'}:
        return True
    if str(proxy.get('type', '')).lower() in {
        'select', 'selector', 'url-test', 'fallback', 'load-balance', 'direct', 'reject', 'dns'
    }:
        return True
    name = re.sub(r'^\s*\[[^]]+\]\s*', '', str(proxy.get('name', ''))).lower()
    words = re.sub(r'[^\w]+', ' ', name).strip()
    if any(token in name for token in [
        'тех. работах', 'техработах', 'maintenance', 'остаток', 'трафик', 'traffic',
        'истека', 'expire', 'информация', 'подписка', 'только tg бот', 'tg бот',
        'купить', 'новости', 'news', 'update'
    ]):
        return True
    if re.search(r'авто\s*выбор|auto\s*select|autoselect|best\s*ping|load\s*balanc|баланс(?:ер|иров)', words):
        return True
    if words in {'balance', 'balancer', 'баланс', 'разделитель', 'separator'}:
        return True
    if re.search(r'\b(?:ниже|выше|далее|below|above)\b.*\b(?:телефон|телефона|телефонов|мобильных|пк|компьютера|phone|mobile|desktop)\b', words):
        return True
    if re.fullmatch(r'(?:для\s+)?(?:телефона|телефонов|мобильных устройств|пк|компьютера|компьютеров)', words):
        return True
    if re.fullmatch(r'(?:for\s+)?(?:phones?|mobile|desktop|pc)(?:\s+only)?', words):
        return True
    # Providers use paired arrows to frame section headings, not VPS names.
    if re.search(r'(?:⏬.*⏬|⏫.*⏫|⬇.*⬇|⬆.*⬆)', name):
        return True
    if re.search(r'если\s+(?:глушат|не\s+работает)\s+интернет', words):
        return True
    return not words


def strip_subscription_placeholders(config):
    proxies = config.get('proxies', [])
    removed = {proxy['name'] for proxy in proxies if is_subscription_placeholder(proxy)}
    config['proxies'] = [proxy for proxy in proxies if proxy['name'] not in removed]
    for group in config.get('proxy-groups', []):
        group['proxies'] = [name for name in group.get('proxies', []) if name not in removed]
    return removed
