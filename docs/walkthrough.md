# Результаты настройки фолбэков и стабилизации IP для Gemini и Antigravity

## Что было реализовано

### 1. Выделенная группа `🤖 Auto-AI-Stable`
В [sync.py](file:///C:/Users/hitsugi%20ni%20ochita/.gemini/antigravity/scratch/mihomo-smart-routing/sync.py) добавлена специализированная группа для AI-сервисов:
- **Тип:** `fallback`
- **Health-check URL:** `https://generativelanguage.googleapis.com`
- **Ожидаемый статус (`expected-status`):** `"404"` (при геоблокировке Google возвращает `403 Forbidden`, что мгновенно отбраковывает ноду).
- **Стабилизация IP:** 
  - `interval: 180` (опрос раз в 3 минуты вместо 20 секунд — предотвращает флаппинг и частую смену IP/стран).
  - `max-failed-times: 3` (failover происходит только при 3 последовательных сбоях, удерживая сессию стабильной).
  - `proxies: ai_proxies` (только чистые ноды US, DE, NL, UK, SE, FI; исключены любые транзитные мосты `Москва → EU` и РФ-ноды).
- **Группа `🤖 AI-Services`** теперь по умолчанию выбирает `🤖 Auto-AI-Stable`.

### 2. Бесконфликтная интеграция с AG Unlocker (`confeden/Antigravity`)
- В [sync.py](file:///C:/Users/hitsugi%20ni%20ochita/.gemini/antigravity/scratch/mihomo-smart-routing/sync.py) в список `fake-ip-filter` добавлены:
  - `cloudcode-pa.googleapis.com`
  - `daily-cloudcode-pa.googleapis.com`
- В `base_rules` (до общих правил Google) добавлены прямые исключения:
  - `DOMAIN,cloudcode-pa.googleapis.com,DIRECT`
  - `DOMAIN,daily-cloudcode-pa.googleapis.com,DIRECT`
  - `IP-CIDR,45.155.204.190/32,DIRECT,no-resolve`
  - `IP-CIDR,83.220.169.155/32,DIRECT,no-resolve`
- **Результат:** Mihomo TUN и Fake-IP не перехватывают трафик Antigravity, позволяя локальной службе `ag_dns` и системному NRPT безошибочно обращаться к серверам разблокировки.

### 3. Расширение детектора блокировок
В [geo_block_detector.py](file:///C:/Users/hitsugi%20ni%20ochita/.gemini/antigravity/scratch/mihomo-smart-routing/geo_block_detector.py) добавлены русскоязычные сигнатуры с защитой от маскирования подстрок:
- `"gemini пока не поддерживается"`
- `"не поддерживается в вашей стране"`
- `"пока не поддерживается"`

### 4. Верификация и тесты
- **Модульные тесты `test_telemetry.py` (5/5 успешно):**
  - Проверка строгого детектирования фразы *"Gemini пока не поддерживается в вашей стране"* и в верхнем регистре.
  - Проверка корректности параметров группы `🤖 Auto-AI-Stable`, порядка правил в `rules` и наличия хостов в `fake-ip-filter`.
- **Полный набор тестов `tests` (23/23 успешно):**
  - Валидация YAML-схемы, парсинга VLESS, дедупликации и реального парсера ядра `verge-mihomo.exe -t`.
- **Аудит безопасности `check_diff.py`:**
  - 0 утечек токенов или персональных данных.

---

## Что нужно сделать пользователю

1. Запустить обновление профиля в **Clash Verge Rev** (или запустить локальный скрипт обновления подписки `update_subscriptions.py`).
2. Убедиться, что в группе **`🤖 AI-Services`** активна группа **`🤖 Auto-AI-Stable`**.
