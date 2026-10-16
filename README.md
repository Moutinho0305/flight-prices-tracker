# Flight Prices Tracker

Vigia preços de voos da Ryanair e avisa no telemóvel (via [ntfy](https://ntfy.sh)) quando mudam. Só precisa de Python 3.

## Como pôr a funcionar

1. **Telemóvel:** instala a app **ntfy**.
2. **Configurar:** `python tracker.py --setup`. O assistente pergunta o tópico (subscreve-o na app ntfy), a rota e o preço-alvo, cria o `config.json` e envia uma notificação de teste.
3. **Correr:** `python tracker.py` (verifica de hora a hora, deixa o terminal aberto)

Alternativa ao passo 3: `python tracker.py --once` faz uma verificação e termina, para usar com o Agendador de Tarefas do Windows ou cron.

Para configurar à mão, copia `config.example.json` para `config.json` e edita. Várias rotas e as opções abaixo só se definem aqui. Para reenviar a notificação de teste: `python tracker.py --test`.

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

Testes: `python -m unittest discover -s tests`

> Usa a API pública não oficial da Ryanair, que pode mudar ou falhar. Projeto independente, sem relação com a Ryanair.
