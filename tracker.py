"""Ryanair price tracker: vigia o voo mais barato por dia e avisa via ntfy quando o preço muda.

Uso:
    python tracker.py --setup    # assistente: cria o config.json e envia uma notificação de teste
    python tracker.py            # corre em loop, de X em X minutos (interval_minutes)
    python tracker.py --once     # uma verificação só (para o Agendador de Tarefas)
    python tracker.py --test     # envia uma notificação de teste
"""
import csv
import json
import re
import secrets
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # consolas Windows (cp1252) não imprimem "→"
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
CONFIG_FILE = BASE / "config.json"
STATE_FILE = BASE / "prices.json"
HISTORY_FILE = BASE / "history.csv"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FAIL_ALERT_AFTER = 3  # falhas seguidas até avisar
API = "https://www.ryanair.com/api/farfnd/v4/"  # + oneWayFares | roundTripFares
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def validate_config(cfg):
    """Levanta ValueError com uma mensagem clara se o config estiver errado."""
    if str(cfg.get("ntfy_topic", "")).strip() in ("", "mete-aqui-um-nome-secreto-e-unico"):
        raise ValueError("Falta o ntfy_topic (o tópico que subscreveste na app ntfy).")
    routes = cfg.get("routes") or ([cfg] if cfg.get("origin") and cfg.get("destination") else [])
    if not routes:
        raise ValueError("Falta pelo menos uma rota em 'routes' (origin e destination).")
    for r in routes:
        for k in ("origin", "destination"):
            if not re.fullmatch(r"[A-Za-z]{3}", str(r.get(k, ""))):
                raise ValueError(f"'{k}' tem de ser um código IATA de 3 letras (ex. OPO), recebi: {r.get(k)!r}")
    for r in [{**cfg, **x} for x in routes]:
        for k in ("date_from", "date_to"):
            if r.get(k):
                try:
                    date.fromisoformat(r[k])
                except ValueError:
                    raise ValueError(f"'{k}' tem de ter o formato AAAA-MM-DD, recebi: {r[k]!r}")
        for w in r.get("weekdays", []):
            if str(w).lower()[:3] not in WEEKDAYS:
                raise ValueError(f"Dia da semana inválido {w!r}. Usa: {', '.join(WEEKDAYS)}")
        if r.get("stay_min_days", 2) > r.get("stay_max_days", 7):
            raise ValueError("stay_min_days não pode ser maior que stay_max_days.")


def load_config():
    if not CONFIG_FILE.exists():
        sys.exit("Ainda não tens config.json. Corre:  python tracker.py --setup")
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        validate_config(cfg)
    except (ValueError, json.JSONDecodeError) as e:
        sys.exit(f"Erro no config.json: {e}\nPodes refazê-lo com:  python tracker.py --setup")
    return cfg


def ask(prompt, default="", check=lambda v: v):
    """Pergunta no terminal até a resposta ser válida. Enter aceita o valor por defeito."""
    while True:
        answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or default
        try:
            return check(answer)
        except ValueError as e:
            print(f"  {e}")


def _nonempty(v):
    if not v:
        raise ValueError("Não pode ficar vazio.")
    return v


def _int(v):
    try:
        return int(v)
    except ValueError:
        raise ValueError("Escreve um número inteiro.")


def _iata(v):
    if not re.fullmatch(r"[A-Za-z]{3}", v):
        raise ValueError("Código de 3 letras, ex. OPO (Porto), LIS (Lisboa), STN (Londres Stansted).")
    return v.upper()


def _yes_no(v):
    if v.lower() not in ("s", "n", "sim", "nao", "não", "y", "yes", "no"):
        raise ValueError("Responde s ou n.")
    return v.lower() in ("s", "sim", "y", "yes")


def _optional_number(v):
    if not v:
        return None
    try:
        return float(v.replace(",", "."))
    except ValueError:
        raise ValueError("Escreve um número (ou Enter para saltar).")


def setup():
    """Assistente interativo: cria o config.json e envia uma notificação de teste."""
    if CONFIG_FILE.exists() and not ask("Já existe um config.json. Substituir? (s/n)", "n", _yes_no):
        return
    print("\n== Configuração do tracker ==\n")
    topic = ask("Nome do tópico ntfy (secreto e único)", f"voos-{secrets.token_hex(4)}", _nonempty)
    print(f"\nNo telemóvel: instala a app ntfy e subscreve o tópico  {topic}")
    input("Quando tiveres feito, carrega Enter... ")
    route = {
        "origin": ask("Aeroporto de origem (código IATA, ex. OPO)", check=_iata),
        "destination": ask("Aeroporto de destino (código IATA, ex. STN)", check=_iata),
    }
    if ask("Ida e volta? (s/n)", "n", _yes_no):
        route["round_trip"] = True
        route["stay_min_days"] = ask("Estadia mínima (dias)", "2", _int)
        route["stay_max_days"] = ask("Estadia máxima (dias)", "7", _int)
    target = ask("Avisar só abaixo de que preço? (Enter = qualquer alteração)", check=_optional_number)
    if target is not None:
        route["target_price"] = target
    cfg = {"ntfy_topic": topic, "currency": "EUR", "interval_minutes": 60, "min_change": 1.0, "routes": [route]}
    validate_config(cfg)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\nconfig.json criado. A enviar notificação de teste...")
    try:
        notify(cfg, "Teste", "O tracker de voos está a funcionar.")
        print("Enviada! Se chegou ao telemóvel, está tudo pronto.")
    except Exception as e:
        print(f"Não consegui enviar ({e}). Verifica a internet e o tópico.")
    print("\nPara começar a vigiar:  python tracker.py")


def routes_from(cfg):
    """Lista de rotas; cada uma herda os campos globais do config e pode sobrepô-los."""
    base = {k: v for k, v in cfg.items() if k != "routes"}
    routes = cfg.get("routes") or [{"origin": cfg["origin"], "destination": cfg["destination"]}]
    return [{**base, **r} for r in routes]


def route_id(r):
    return f"{r['origin']}-{r['destination']}" + ("-rt" if r.get("round_trip") else "")


def fetch_fares(r):
    """Devolve {chave: preço}. Chave = 'AAAA-MM-DD' (ida) ou 'AAAA-MM-DD/AAAA-MM-DD' (ida e volta, preço total)."""
    today = date.today()
    start = max(today, date.fromisoformat(r["date_from"])) if r.get("date_from") else today
    end = date.fromisoformat(r["date_to"]) if r.get("date_to") else start + timedelta(days=90)
    params = {
        "departureAirportIataCode": r["origin"],
        "arrivalAirportIataCode": r["destination"],
        "outboundDepartureDateFrom": start.isoformat(),
        "outboundDepartureDateTo": end.isoformat(),
        "currency": r.get("currency", "EUR"),
    }
    round_trip = r.get("round_trip")
    if round_trip:
        lo, hi = r.get("stay_min_days", 2), r.get("stay_max_days", 7)
        params.update({
            "inboundDepartureDateFrom": (start + timedelta(days=lo)).isoformat(),
            "inboundDepartureDateTo": (end + timedelta(days=hi)).isoformat(),
            "durationFrom": lo,
            "durationTo": hi,
        })
    else:
        params["limit"] = 100
    url = f"{API}{'roundTripFares' if round_trip else 'oneWayFares'}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as resp:
        data = json.load(resp)
    weekdays = {WEEKDAYS.index(d.lower()[:3]) for d in r.get("weekdays", [])}
    prices = {}
    for f in data.get("fares", []):
        day = f["outbound"]["departureDate"][:10]
        if weekdays and date.fromisoformat(day).weekday() not in weekdays:
            continue
        if round_trip:
            prices[f"{day}/{f['inbound']['departureDate'][:10]}"] = f["summary"]["price"]["value"]
        else:
            prices[day] = f["outbound"]["price"]["value"]
    return prices


def notify(cfg, title, message, priority=3, click=None):
    url = f"{cfg.get('ntfy_server', 'https://ntfy.sh')}/{cfg['ntfy_topic']}"
    headers = {"Title": title.encode("utf-8"), "Tags": "airplane", "Priority": str(priority)}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers)
    urllib.request.urlopen(req, timeout=30).read()


def booking_link(r, key):
    out, _, back = key.partition("/")
    return (
        "https://www.ryanair.com/pt/pt/trip/flights/select?adults=1&teens=0&children=0&infants=0"
        f"&dateOut={out}&isReturn={'true' if back else 'false'}"
        + (f"&dateIn={back}" if back else "")
        + f"&originIata={r['origin']}&destinationIata={r['destination']}"
    )


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def historical_min(route_key):
    """Preço mais baixo já registado para a rota (None se ainda não há histórico)."""
    if not HISTORY_FILE.exists():
        return None
    with HISTORY_FILE.open(encoding="utf-8", newline="") as f:
        prices = [float(row["price"]) for row in csv.DictReader(f) if row["route"] == route_key]
    return min(prices) if prices else None


def append_history(route_key, rows):
    new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "route", "day", "price"])
        now = datetime.now().isoformat(timespec="seconds")
        for day, price in rows:
            w.writerow([now, route_key, day, price])


def check(cfg, r, state):
    route_key = route_id(r)
    route = f"{r['origin']}⇄{r['destination']}" if r.get("round_trip") else f"{r['origin']}→{r['destination']}"
    cur = r.get("currency", "EUR")
    target = r.get("target_price")
    new = fetch_fares(r)
    old = state.get(route_key)
    prev_min = historical_min(route_key)

    if not new:
        print(f"[{datetime.now():%H:%M}] {route}: sem voos no período")
        return
    cheapest_day = min(new, key=new.get)
    cheapest = new[cheapest_day]
    print(f"[{datetime.now():%H:%M}] {route}: {len(new)} opções, mais barato {cheapest:.2f} {cur} ({cheapest_day})")

    changed_rows = [(d, p) for d, p in new.items() if old is None or old.get(d) != p]
    state[route_key] = new
    append_history(route_key, changed_rows)

    if old is None:  # primeira execução: só guarda a base
        extra = f"\nAlerta quando baixar de {target:.2f} {cur}" if target else ""
        notify(cfg, f"A vigiar {route}", f"Mais barato agora: {cheapest:.2f} {cur} em {cheapest_day}{extra}",
               click=booking_link(r, cheapest_day))
        return

    min_diff = r.get("min_change", 1.0)
    changes = []
    for day, price in sorted(new.items()):
        prev = old.get(day)
        if prev is None:
            changes.append((price, abs(price), f"{day}: novo voo a {price:.2f} {cur}"))
        elif abs(price - prev) >= min_diff:
            arrow = "⬇️" if price < prev else "⬆️"
            changes.append((price, abs(price - prev), f"{arrow} {day}: {prev:.2f} → {price:.2f} {cur}"))
    for day in old.keys() - new.keys():
        if date.fromisoformat(day[:10]) >= date.today():
            changes.append((float("inf"), 0, f"{day}: voo já não disponível (era {old[day]:.2f} {cur})"))

    record_low = prev_min is not None and cheapest < prev_min
    if target is not None:  # com preço-alvo, só interessam alterações a ou abaixo do alvo
        changes = [c for c in changes if c[0] <= target]
    if not changes and not record_low:
        return

    changes.sort(key=lambda c: -c[1])
    lines = []
    if record_low:
        lines.append(f"🏆 Novo mínimo histórico: {cheapest:.2f} {cur} (antes {prev_min:.2f})")
    lines += [c[2] for c in changes[:8]]
    if len(changes) > 8:
        lines.append(f"... e mais {len(changes) - 8} alterações")
    lines.append(f"Mais barato: {cheapest:.2f} {cur} ({cheapest_day})")
    hit_target = target is not None and cheapest <= target
    notify(cfg, f"Ryanair {route}: {'preço-alvo atingido' if hit_target else 'preços mudaram'}",
           "\n".join(lines), priority=4 if (record_low or hit_target) else 3,
           click=booking_link(r, cheapest_day))
    print(f"  {len(changes)} alterações notificadas")


def main():
    if "--setup" in sys.argv:
        setup()
        return
    cfg = load_config()
    if "--test" in sys.argv:
        notify(cfg, "Teste", "O tracker de voos está a funcionar.")
        return
    while True:
        state = load_state()
        fails = state.setdefault("_failures", {})
        for r in routes_from(cfg):
            key = route_id(r)
            try:
                check(cfg, r, state)
                if fails.pop(key, 0) >= FAIL_ALERT_AFTER:
                    notify(cfg, f"{key} recuperou", "O tracker voltou a conseguir ler os preços.")
            except Exception as e:  # rede em baixo, API bloqueou, etc: tenta na próxima volta
                print(f"[{datetime.now():%H:%M}] {key} erro: {e}")
                fails[key] = fails.get(key, 0) + 1
                if fails[key] == FAIL_ALERT_AFTER:  # avisa uma só vez, não a cada falha
                    try:
                        notify(cfg, f"{key}: tracker com problemas",
                               f"{FAIL_ALERT_AFTER} falhas seguidas. Último erro: {e}", priority=4)
                    except Exception:
                        pass  # se nem o ntfy chega, não há muito a fazer
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        if "--once" in sys.argv:
            break
        time.sleep(cfg.get("interval_minutes", 60) * 60)


if __name__ == "__main__":
    main()
