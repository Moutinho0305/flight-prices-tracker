# Flight Prices Tracker

Vigia preços de voos da Ryanair e avisa no telemóvel (via [ntfy](https://ntfy.sh)) quando mudam. Só precisa de Python 3.

## Como pôr a funcionar

1. **Telemóvel:** instala a app **ntfy** e subscreve um tópico com nome secreto e único (ex. `voos-joao-x7k2q`).
2. **Config:** copia `config.example.json` para `config.json` e preenche:
   - `ntfy_topic`: o tópico do passo 1
   - `routes`: origem e destino em códigos IATA (ex. `OPO` → `STN`). Podes pôr várias rotas.
   - `target_price` (opcional): só avisa quando o preço for igual ou inferior
3. **Testar:** `python tracker.py --test` (deve chegar uma notificação)
4. **Correr:** `python tracker.py` (verifica de hora a hora, deixa o terminal aberto)

Alternativa: `python tracker.py --once` faz uma verificação e termina, para usar com o Agendador de Tarefas do Windows ou cron.

## Opções úteis (`config.json`)

| Campo | O que faz |
|---|---|
| `interval_minutes` | Intervalo entre verificações (por defeito 60) |
| `date_from` / `date_to` | Datas a vigiar, `YYYY-MM-DD` (por defeito, próximos 90 dias) |
| `min_change` | Variação mínima de preço para avisar (por defeito 1) |
| `weekdays` | Só dias de partida indicados, ex. `["fri", "sun"]` (por defeito, todos) |
| `round_trip` | `true` para vigiar ida e volta (preço total) em vez de só ida |
| `stay_min_days` / `stay_max_days` | Duração da estadia em ida e volta (por defeito 2 a 7 dias) |

Também te avisa quando surge um novo mínimo histórico, e quando o tracker falha 3 vezes seguidas (ex. API bloqueada). O histórico fica em `history.csv`.

> Usa a API pública não oficial da Ryanair, que pode mudar ou falhar. Projeto independente, sem relação com a Ryanair.
