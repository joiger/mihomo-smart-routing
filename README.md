# 🚀 Mihomo Smart Routing & Multi-Subscription Merger

Универсальный инструмент для объединения нескольких VPN-подписок (VLESS, Reality, xHTTP, gRPC, Trojan, SS) в единый конфигурационный файл для **Clash Verge Rev**, **FlClash** и **Mihomo (Clash.Meta)** с независимой параллельной маршрутизацией.

---

## ✨ Возможности

* 🤖 **AI-Services (Gemini, Claude, ChatGPT, Antigravity):**
  * Автоматический опрос эндпоинтов Google AI (`generativelanguage.googleapis.com`) каждые 5 минут.
  * Приоритетный пул гарантированно поддерживаемых стран (США 🇺🇸, Германия 🇩🇪, Нидерланды 🇳🇱, Великобритания 🇬🇧).
  * Автоматическое переключение узла при гео-блокировках без участия пользователя.
* 🎬 **Media-Streaming (YouTube, SoundCloud):**
  * Выделенный высокоскоростной пул с регулярной проверкой задержки для бесшовного воспроизведения музыки и видео в 4K.
* 💬 **Discord:**
  * Полноценная поддержка голосовых каналов (UDP), звонков и медиа без зависаний на «Подключение к RTC».
* 🎯 **Онлайн-игры (Steam, Epic Games, Riot / Valorant, Blizzard, EA):**
  * По умолчанию направлены **напрямую (`DIRECT`)** для минимального пинга (15–40 мс) и защиты от блокировок античитами (EasyAntiCheat, Vanguard).
  * Возможность переключить на прокси в 1 клик, если конкретная игра заблокирована провайдером.
* 🇷🇺 **Российские сервисы и банки (DIRECT):**
  * Сбер, Т-Банк, Госуслуги, VK, Яндекс, Ozon, Wildberries и домашняя локальная сеть работают напрямую на полной скорости.
* 🛡️ **Поддержка защиты Remnawave HWID и Anti-Bot Cookies:**
  * Умеет выгружать узлы из панелей, требующих заголовок `x-hwid` (Hiddify/Happ), а также из подписок с защитой от ботов (HTTP 307 + cookie jar).

---

## 📂 Структура проекта

```text
├── config.example.json   # Шаблон настроек подписок
├── sync.py               # Главный генератор и объединитель конфигов
├── update.bat            # Скрипт обновления в 1 клик для Windows
├── update.sh             # Скрипт обновления для Linux / macOS
├── .gitignore            # Защита от утечки ваших личных ключей и ссылок
└── README.md             # Документация
```

---

## 🚀 Быстрый старт

### 1. Клонируйте репозиторий
```bash
git clone https://github.com/ВАШ_НИК/mihomo-smart-routing.git
cd mihomo-smart-routing
```

### 2. Настройте свои подписки
Скопируйте файл примера в рабочий `config.json`:
```bash
cp config.example.json config.json
```
*(на Windows: `copy config.example.json config.json`)*

Откройте `config.json` в любом текстовом редакторе и вставьте свои ссылки на подписки:
```json
{
  "subscriptions": [
    {
      "name": "Provider_1",
      "url": "https://your-subscription-url-1.com/token",
      "headers": {
        "User-Agent": "Clash-verge/1.7.7",
        "x-hwid": "a1b2c3d4e5f60718293a"
      },
      "use_cookies": false
    },
    {
      "name": "Provider_2",
      "url": "https://your-subscription-url-2.com/token",
      "headers": {
        "User-Agent": "Hiddify/2.0.5"
      },
      "use_cookies": true
    }
  ],
  "options": {
    "mixed_port": 7890,
    "mode": "rule",
    "output_files": [
      "./3-in-1_VPN.yaml",
      "~/Desktop/3-in-1_VPN.yaml"
    ]
  }
}
```

### 3. Запустите генерацию
* **Windows:** Двойной клик по **`update.bat`** (или `python sync.py`).
* **Linux / macOS:** `./update.sh` (или `python3 sync.py`).

Скрипт скачает узлы, проверит их, удалит дубликаты, настроит интеллектуальные правила и создаст готовый файл **`3-in-1_VPN.yaml`**.

---

## 💻 Использование

### На ПК (Clash Verge Rev):
1. Откройте **Clash Verge Rev** ➔ раздел **«Профили»**.
2. Нажмите кнопку **«Импортировать»** ➔ выберите сгенерированный файл `3-in-1_VPN.yaml`.
3. Активируйте профиль и включите **«Режим TUN»** или **«Системный прокси»**.

### На Android (FlClash / Clash Meta for Android):
1. Перекиньте файл `3-in-1_VPN.yaml` на смартфон (например, через избранное в Telegram).
2. В приложении **FlClash** перейдите в **«Профили»** ➔ **«+»** ➔ **«Импорт из файла»**.
3. Нажмите кнопку **«Подключиться»**.

---

## 🔒 Безопасность
Файл `.gitignore` уже настроен так, чтобы ваши приватные ссылки, токены, HWID и сгенерированные файлы `*.yaml` **ни при каких условиях не попали в публичный репозиторий GitHub**.

---

## 📄 Лицензия
Проект распространяется под лицензией **GNU General Public License v3.0 (GPLv3)**. Подробности см. в файле [LICENSE](LICENSE).
