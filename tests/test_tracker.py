import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tracker as t  # noqa: E402


def d(n):
    return (date.today() + timedelta(days=n)).isoformat()


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return json.dumps(self.payload).encode()


def one_way(*pairs):
    return {"fares": [{"outbound": {"departureDate": f"{day}T10:00:00", "price": {"value": p}}} for day, p in pairs]}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(t, "STATE_FILE", tmp / "prices.json"),
            mock.patch.object(t, "HISTORY_FILE", tmp / "history.csv"),
            mock.patch.object(t, "CONFIG_FILE", tmp / "config.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tmp
        self.sent = []
        p = mock.patch.object(
            t, "notify",
            lambda cfg, title, msg, priority=3, click=None: self.sent.append((title, priority, msg, click)),
        )
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"ntfy_topic": "x", "routes": [{"origin": "OPO", "destination": "STN"}]}

    def run_check(self, cfg, fares):
        r = t.routes_from(cfg)[0]
        with mock.patch.object(t, "fetch_fares", lambda r: dict(fares)):
            state = t.load_state()
            t.check(cfg, r, state)
            t.STATE_FILE.write_text(json.dumps(state))


class TestFetch(unittest.TestCase):
    def test_one_way_and_weekday_filter(self):
        fri = next(d(i) for i in range(1, 8) if date.fromisoformat(d(i)).weekday() == 4)
        sat = (date.fromisoformat(fri) + timedelta(days=1)).isoformat()
        payload = one_way((fri, 20), (sat, 10))
        r = {"origin": "OPO", "destination": "STN", "weekdays": ["fri"]}
        with mock.patch.object(t.urllib.request, "urlopen", lambda *a, **k: FakeResponse(payload)):
            self.assertEqual(t.fetch_fares(r), {fri: 20})

    def test_round_trip_key_and_total_price(self):
        payload = {"fares": [{"outbound": {"departureDate": "2030-01-05T10:00:00"},
                              "inbound": {"departureDate": "2030-01-08T10:00:00"},
                              "summary": {"price": {"value": 52.12}}}]}
        r = {"origin": "OPO", "destination": "STN", "round_trip": True}
        seen = []
        with mock.patch.object(t.urllib.request, "urlopen",
                               lambda req, **k: seen.append(req.full_url) or FakeResponse(payload)):
            self.assertEqual(t.fetch_fares(r), {"2030-01-05/2030-01-08": 52.12})
        self.assertIn("roundTripFares", seen[0])
        self.assertIn("durationFrom=2", seen[0])

    def test_route_id_and_link(self):
        self.assertEqual(t.route_id({"origin": "OPO", "destination": "STN"}), "OPO-STN")
        self.assertEqual(t.route_id({"origin": "OPO", "destination": "STN", "round_trip": True}), "OPO-STN-rt")
        r = {"origin": "OPO", "destination": "STN"}
        self.assertIn("isReturn=false", t.booking_link(r, "2030-01-05"))
        link = t.booking_link(r, "2030-01-05/2030-01-08")
        self.assertIn("isReturn=true", link)
        self.assertIn("dateIn=2030-01-08", link)

    def test_routes_inherit_global_and_legacy_single_route(self):
        cfg = {"ntfy_topic": "x", "currency": "USD",
               "routes": [{"origin": "A", "destination": "B", "currency": "EUR"}, {"origin": "C", "destination": "D"}]}
        rs = t.routes_from(cfg)
        self.assertEqual([r["currency"] for r in rs], ["EUR", "USD"])
        legacy = t.routes_from({"ntfy_topic": "x", "origin": "OPO", "destination": "STN"})
        self.assertEqual(legacy[0]["destination"], "STN")


class TestCheck(Base):
    def test_first_run_notifies_baseline_and_saves_history(self):
        self.run_check(self.cfg, {d(5): 20.0, d(6): 30.0})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("A vigiar", self.sent[0][0])
        self.assertEqual(len(t.HISTORY_FILE.read_text().strip().splitlines()), 3)  # header + 2

    def test_no_change_no_notification(self):
        self.run_check(self.cfg, {d(5): 20.0})
        self.run_check(self.cfg, {d(5): 20.0})
        self.assertEqual(len(self.sent), 1)

    def test_change_below_min_change_is_ignored(self):
        self.run_check(self.cfg, {d(5): 20.0, d(6): 30.0})
        self.run_check(self.cfg, {d(5): 20.5, d(6): 30.0})
        self.assertEqual(len(self.sent), 1)

    def test_price_change_notifies_with_record_low(self):
        self.run_check(self.cfg, {d(5): 20.0, d(6): 30.0})
        self.run_check(self.cfg, {d(5): 20.0, d(6): 25.0})
        self.assertEqual(len(self.sent), 2)  # alteração normal
        self.assertNotIn("mínimo histórico", self.sent[-1][2])  # 20 ainda é o mínimo
        self.run_check(self.cfg, {d(5): 15.0, d(6): 25.0})
        title, prio, msg, click = self.sent[-1]
        self.assertIn("mínimo histórico", msg)
        self.assertEqual(prio, 4)
        self.assertTrue(click.startswith("https://www.ryanair.com"))

    def test_target_price_filters_changes(self):
        cfg = {"ntfy_topic": "x", "routes": [{"origin": "OPO", "destination": "STN", "target_price": 20}]}
        self.run_check(cfg, {d(5): 40.0, d(6): 50.0})
        self.run_check(cfg, {d(5): 45.0, d(6): 50.0})  # acima do alvo: silêncio
        self.assertEqual(len(self.sent), 1)
        self.run_check(cfg, {d(5): 18.0, d(6): 50.0})  # abaixo do alvo
        self.assertEqual(len(self.sent), 2)
        self.assertIn("preço-alvo", self.sent[-1][0])

    def test_vanished_and_new_flights_reported(self):
        self.run_check(self.cfg, {d(5): 20.0, d(6): 30.0})
        self.run_check(self.cfg, {d(5): 20.0, d(7): 33.0})
        msg = self.sent[-1][2]
        self.assertIn("novo voo", msg)
        self.assertIn("já não disponível", msg)

    def test_round_trip_keys_work_in_check(self):
        cfg = {"ntfy_topic": "x", "routes": [{"origin": "OPO", "destination": "STN", "round_trip": True}]}
        k = f"{d(5)}/{d(8)}"
        self.run_check(cfg, {k: 52.0})
        self.run_check(cfg, {k: 40.0})
        self.assertIn("⇄", self.sent[-1][0])
        self.assertIn("isReturn=true", self.sent[-1][3])

    def test_empty_result_is_silent(self):
        self.run_check(self.cfg, {})
        self.assertEqual(self.sent, [])


class TestValidation(unittest.TestCase):
    def bad(self, cfg):
        with self.assertRaises(ValueError):
            t.validate_config(cfg)

    def test_valid_configs(self):
        t.validate_config({"ntfy_topic": "abc", "routes": [{"origin": "OPO", "destination": "stn"}]})
        t.validate_config({"ntfy_topic": "abc", "origin": "OPO", "destination": "STN"})  # formato antigo
        t.validate_config({"ntfy_topic": "abc", "date_from": "2030-01-01", "weekdays": ["Fri", "sunday"],
                           "routes": [{"origin": "OPO", "destination": "STN", "round_trip": True}]})

    def test_invalid_configs(self):
        ok = [{"origin": "OPO", "destination": "STN"}]
        self.bad({"routes": ok})  # sem tópico
        self.bad({"ntfy_topic": "mete-aqui-um-nome-secreto-e-unico", "routes": ok})
        self.bad({"ntfy_topic": "abc", "routes": []})
        self.bad({"ntfy_topic": "abc", "routes": [{"origin": "Porto", "destination": "STN"}]})
        self.bad({"ntfy_topic": "abc", "date_from": "01-12-2030", "routes": ok})
        self.bad({"ntfy_topic": "abc", "weekdays": ["xyz"], "routes": ok})
        self.bad({"ntfy_topic": "abc", "stay_min_days": 9, "stay_max_days": 3, "routes": ok})

    def test_load_config_exits_with_helpful_message(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(t, "CONFIG_FILE", Path(tmp) / "config.json"):
            with self.assertRaises(SystemExit) as cm:
                t.load_config()
            self.assertIn("--setup", str(cm.exception))


class TestSetup(Base):
    def run_setup(self, answers):
        with mock.patch("builtins.input", side_effect=answers):
            t.setup()
        return json.loads(t.CONFIG_FILE.read_text(encoding="utf-8"))

    def test_setup_one_way_with_retry_on_bad_input(self):
        cfg = self.run_setup(["meu-topico", "", "porto", "opo", "stn", "n", "25,5"])
        self.assertEqual(cfg["ntfy_topic"], "meu-topico")
        self.assertEqual(cfg["routes"], [{"origin": "OPO", "destination": "STN", "target_price": 25.5}])
        self.assertEqual(self.sent[0][0], "Teste")
        t.validate_config(cfg)

    def test_setup_round_trip_defaults_and_generated_topic(self):
        cfg = self.run_setup(["", "", "OPO", "BCN", "s", "", "4", ""])
        self.assertTrue(cfg["ntfy_topic"].startswith("voos-"))
        route = cfg["routes"][0]
        self.assertEqual((route["round_trip"], route["stay_min_days"], route["stay_max_days"]), (True, 2, 4))
        self.assertNotIn("target_price", route)

    def test_setup_does_not_overwrite_without_confirmation(self):
        t.CONFIG_FILE.write_text('{"keep": true}')
        with mock.patch("builtins.input", side_effect=[""]):  # Enter = defeito "n"
            t.setup()
        self.assertEqual(t.CONFIG_FILE.read_text(), '{"keep": true}')


class TestMainLoop(Base):
    def test_failure_alert_once_then_recovery(self):
        self.cfg["interval_minutes"] = 0
        (self.tmp / "config.json").write_text(json.dumps(self.cfg))
        outcomes = iter([OSError("x")] * 4 + [one_way((d(5), 20.0))])

        def fake_urlopen(*a, **k):
            o = next(outcomes)
            if isinstance(o, Exception):
                raise o
            return FakeResponse(o)

        sleeps = []

        def fake_sleep(_):
            sleeps.append(1)
            if len(sleeps) == 5:
                raise KeyboardInterrupt

        with mock.patch.object(t.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(t.time, "sleep", fake_sleep), mock.patch.object(sys, "argv", ["tracker.py"]):
            with self.assertRaises(KeyboardInterrupt):
                t.main()
        titles = [s[0] for s in self.sent]
        self.assertEqual(sum("problemas" in x for x in titles), 1)
        self.assertEqual(sum("recuperou" in x for x in titles), 1)


if __name__ == "__main__":
    unittest.main()
