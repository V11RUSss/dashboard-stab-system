# BRO30 Dashboard

Система мониторинга микроконтроллеров BRO30 в реальном времени.
Принимает данные по raw Ethernet (кастомный MicroJet-протокол), парсит и отображает в браузере.

---

## Как это работает

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   [BRO30 / bro_monitor]                                             │
│        │                                                            │
│        │  raw Ethernet multicast (кастомный UDP-протокол MicroJet)  │
│        │  239.200.100.1:49001  →  DCU Telemetry                     │
│        │  239.200.10.1:49002   →  BUP Drive State                   │
│        ▼                                                            │
│   [bro30_gateway — C++]          (container, network_mode: host)    │
│        │  raw socket PF_PACKET                                      │
│        │  Принимает Ethernet-фреймы напрямую с NIC                  │
│        │  Парсит MicroJet-протокол через microjetlinux              │
│        │  Упаковывает данные в TLV-бинарный формат                  │
│        │  Отправляет по TCP на localhost:8888                        │
│        ▼                                                            │
│   [bro30_backend — Python]       (container, network_mode: host)    │
│        │  Принимает TLV-фреймы от gateway                           │
│        │  Декодирует C-структуры (ctypes)                           │
│        │  Хранит историю телеметрии                                 │
│        │  Раздаёт данные по WebSocket на localhost:8765              │
│        ▼                                                            │
│   [bro30_frontend — Dash/Python] (container, network_mode: host)    │
│        │  Получает обновления по WebSocket                           │
│        │  Отображает устройства, графики, статусы                   │
│        │  Доступен в браузере на http://localhost:8050               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Три компонента

| Контейнер | Язык | Роль |
|---|---|---|
| `bro30_gateway` | C++ | Слушает raw Ethernet, разбирает MicroJet-протокол, шлёт TLV по TCP |
| `bro30_backend` | Python | Принимает TLV, декодирует структуры, раздаёт WebSocket |
| `bro30_frontend` | Python/Dash | Браузерный UI с графиками и устройствами |

Все контейнеры работают с `network_mode: host` — никакой сетевой изоляции Docker,
все порты напрямую доступны на хосте.

---

## Быстрый старт

### 1. Узнать имя сетевого интерфейса

Gateway принимает пакеты через raw socket — нужно указать **точное имя** интерфейса,
на который приходят пакеты от BRO30:

```bash
ip link show
```

Типичные имена: `eth0`, `enp3s0`, `ens33`, `wlan0`.

### 2. Запустить

```bash
cd /home/daniil/Projects/Dashboard/deploy

export IFACE=wlan0      # ← подставить своё имя интерфейса
docker compose up --build
```

### 3. Открыть в браузере

```
http://localhost:8050
```

---

## Тестирование с bro_monitor

`bro_monitor` — это симулятор BRO30, работающий как отдельная программа.
Находится в `/home/daniil/Projects/fake_monitor/`.

### Подготовка bro_monitor (один раз)

```bash
# 1. Установить зависимости (qt5-charts собирается из AUR)
yay -S qt5-gamepad
# qt5-charts — сборка из AUR с патчем для GCC 16:
cd ~/.cache/yay/qt5-charts/src/build
sudo make install && sudo ldconfig

# 2. Дать права на raw socket (один раз, сохраняется)
sudo setcap cap_net_raw+ep /home/daniil/Projects/fake_monitor/bro_monitor

# 3. Добавить libpython3.12 из pyenv в путь
export LD_LIBRARY_PATH="$HOME/.pyenv/versions/3.12.9/lib:$LD_LIBRARY_PATH"
```

---

### Вариант A: два ноутбука через Ethernet кабель ✅ (рекомендуется для реального железа)

```
[Ноутбук 1]  bro_monitor  ──── Ethernet кабель ────  [Ноутбук 2]  Dashboard
```

**На ноутбуке 2** (Dashboard):
```bash
export IFACE=eth0   # интерфейс, подключённый к ноутбуку 1
cd deploy && docker compose up --build
```

**На ноутбуке 1** (bro_monitor):
```bash
export LD_LIBRARY_PATH="$HOME/.pyenv/versions/3.12.9/lib:$LD_LIBRARY_PATH"
/home/daniil/Projects/fake_monitor/bro_monitor
# В UI выбрать интерфейс — тот что смотрит на ноутбук 2
```

---

### Вариант B: один ноутбук через виртуальный интерфейс veth ✅ (для разработки)

Raw socket работает с `veth`-парой — трафик гоняется внутри ядра, физическая NIC не нагружается.

```bash
# ШАГ 1: Создать veth пару (сбрасывается при перезагрузке)
sudo ip link add veth0 type veth peer name veth1
sudo ip link set veth0 up
sudo ip link set veth1 up

# ШАГ 2: Dashboard слушает на veth0
export IFACE=veth0
cd /home/daniil/Projects/Dashboard/deploy
docker compose up --build

# ШАГ 3: bro_monitor отправляет через veth1
export LD_LIBRARY_PATH="$HOME/.pyenv/versions/3.12.9/lib:$LD_LIBRARY_PATH"
/home/daniil/Projects/fake_monitor/bro_monitor
# В UI bro_monitor выбрать интерфейс: veth1
```

> После перезагрузки нужно пересоздать veth пару (ШАГ 1).

---

## Порты

| Порт | Протокол | Что слушает |
|------|----------|-------------|
| `8050` | HTTP | Frontend (Dash UI) — открыть в браузере |
| `8765` | WebSocket | Backend → Frontend (живые данные) |
| `8888` | TCP | Gateway → Backend (TLV-фреймы) |

---

## Логи контейнеров

```bash
# Смотреть все логи в реальном времени
docker compose logs -f

# Только gateway (приём пакетов)
docker logs -f bro30_gateway

# Только backend (декодирование и WS)
docker logs -f bro30_backend
```

### Расшифровка типичных логов

| Сообщение | Что означает |
|---|---|
| `[REAL] Listening for MicroJet packets...` | Gateway запущен, ждёт пакеты |
| `[TCP] Connected to 127.0.0.1:8888` | Gateway подключился к backend ✅ |
| `Шлюз подключился: ('127.0.0.1', ...)` | Backend принял gateway ✅ |
| `MJDRV: Error in recvfrom(): Resource temporarily unavailable` | Нет пакетов — **это нормально**, так работает polling |
| `MJDRV: CRITICAL: No such device` | Неверный интерфейс → проверь `IFACE` |
| `MJDRV: RX thread is running on CPU 1` | Поток приёма запущен ✅ |
| `Новое WS-подключение` | Браузер открыл дашборд ✅ |

---

## Переменные окружения

| Переменная | Сервис | По умолчанию | Описание |
|---|---|---|---|
| `IFACE` | gateway | `wlan0` | Сетевой интерфейс для raw socket |
| `BACKEND_ADDR` | gateway | `127.0.0.1` | Адрес backend (TCP) |
| `BACKEND_PORT` | gateway | `8888` | Порт backend (TCP) |
| `WEBSOCKET_URL` | frontend | `ws://localhost:8765` | URL WebSocket-сервера |

Пример переопределения:
```bash
IFACE=enp3s0 docker compose up --build
```

---

## Структура проекта

```
Dashboard/
├── deploy/
│   └── docker-compose.yml      # главный файл запуска
├── gateway/                    # C++ шлюз
│   ├── src/
│   │   ├── main.cpp            # точка входа, выбор режима real/sim
│   │   ├── real_receiver.cpp   # raw socket через microjetlinux
│   │   ├── tcp_forwarder.cpp   # TCP-клиент → backend
│   │   └── packet_handlers.cpp # упаковка данных в TLV
│   ├── microjetlinux/          # драйвер raw Ethernet (сторонний)
│   ├── microjetlib/            # MicroJet protocol library (сторонний)
│   ├── CMakeLists.txt
│   └── Dockerfile
├── backend/                    # Python backend
│   ├── main.py                 # asyncio event loop
│   └── core/
│       ├── tcp_server.py       # приём TLV от gateway
│       ├── mj_decoder.py       # декодирование C-структур (ctypes)
│       ├── ws_server.py        # WebSocket → frontend
│       └── storage.py          # хранение истории
├── frontend/                   # Python/Dash frontend
│   ├── main.py                 # Dash app
│   └── tabs/
│       ├── devices.py          # вкладка "Устройства"
│       ├── graph.py            # вкладка "График"
│       └── editor.py           # вкладка "Скрипты"
└── README.md
```

---

## TLV-протокол (gateway → backend)

Каждый пакет от gateway — это TLV-фрейм:

```
┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬───────────────┐
│ type     │ src_id   │ dst_id   │ ts_s     │ ts_ns    │ length   │ data          │
│ uint8    │ uint16   │ uint16   │ uint32   │ uint32   │ uint16   │ bytes[length] │
│ 1 байт   │ 2 байта  │ 2 байта  │ 4 байта  │ 4 байта  │ 2 байта  │ N байт        │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────┴───────────────┘

type: 0x01 = DCU Telemetry
      0x02 = BUP Drive State
```

`data` — это бинарная C-структура (`_DcuTelemetry` или `_BupDrvState`),
декодируется в backend через `ctypes`.
