"""End-to-end through the HTTP API with the fake model: import -> run -> appeal -> decision -> checklist edit."""
import json
import time

from fastapi.testclient import TestClient

from app.main import app


def wait_run(cli, rid):
    for _ in range(200):
        r = cli.get(f"/api/runs/{rid}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_full_demo_flow():
    recs = [{"conversation_id": f"f-{i}", "channel": "agent chat", "language": "RU", "audio_url": None,
             "transcript": [{"timestamp": "00:01", "speaker": "client", "text": f"Где мой тариф? ({i})"},
                            {"timestamp": "00:05", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина."},
                            {"timestamp": "00:20", "speaker": "operator", "text": "Откройте «Кабинет». До свидания!"}]}
            for i in range(3)]
    with TestClient(app) as cli:
        up = cli.post("/api/datasets/upload", files={"file": ("d.json", json.dumps(recs))}, data={"group_id": "FLOW"})
        assert up.status_code == 200 and up.json()["imported"] == 3

        run = wait_run(cli, cli.post("/api/runs", json={"dataset_group_id": "FLOW"}).json()["id"])
        assert run["status"] == "done" and run["done"] == 24 and run["new_calls"] == 24

        # same run again: everything from cache
        run2 = wait_run(cli, cli.post("/api/runs", json={"dataset_group_id": "FLOW"}).json()["id"])
        assert run2["cache_hits"] == 24 and run2["new_calls"] == 0

        view = cli.get("/api/conversations/f-0").json()["view"]
        assert view["breakdown"]["total"] == 100.0
        ident = next(i for i in view["items"] if i["criterion"]["id"] == "identification")
        assert ident["final"]["verdict"] == "na" and ident["result"]["na_reason"]

        # appeal + decision -> new total, original untouched
        contact = next(i for i in view["items"] if i["criterion"]["id"] == "contact")
        a = cli.post(f"/api/criterion-results/{contact['result']['id']}/appeals", json={"comment": "Не представился"}).json()
        assert cli.get("/api/appeals").json()[0]["excerpt"]
        bad = cli.post(f"/api/appeals/{a['id']}/decision", json={"action": "changed", "new_verdict": "maybe", "reason": "x"})
        assert bad.status_code == 400 and "error" in bad.json()
        ok = cli.post(f"/api/appeals/{a['id']}/decision", json={"action": "changed", "new_verdict": "no", "reason": "Имени нет"})
        assert ok.status_code == 200
        view = cli.get("/api/conversations/f-0").json()["view"]
        contact = next(i for i in view["items"] if i["criterion"]["id"] == "contact")
        assert contact["original"]["verdict"] == "yes" and contact["final"]["verdict"] == "no"
        assert view["original_total"] == 100.0 and view["breakdown"]["total"] == 87.5  # 70/80, identification N/A

        # edit one criterion -> new version -> only that criterion re-runs
        cl = cli.get("/api/checklist").json()["active"]["checklist"]
        cl["criteria"][4]["yes_rule"] += " Новая формулировка."
        saved = cli.post("/api/checklist/versions", json={"checklist": cl, "note": "замена критерия"}).json()
        assert saved["changed_criteria"] == ["empathy"]
        run3 = wait_run(cli, cli.post("/api/runs", json={"dataset_group_id": "FLOW"}).json()["id"])
        assert run3["new_calls"] == 3 and run3["cache_hits"] == 21

        # invalid checklist is rejected with a readable message
        cl["criteria"][0]["weight"] = -1
        r = cli.post("/api/checklist/versions", json={"checklist": cl})
        assert r.status_code == 400 and "вес" in r.json()["detail"]

        assert cli.get("/api/export/FLOW.csv").status_code == 200
        assert cli.get("/api/stats/cost").json()["calls"] == 27


def test_focus_lines_in_prompt():
    from app.judge import criterion_block
    utts = [{"uid": "u1", "idx": 1, "speaker": "client", "text": "Привет"},
            {"uid": "u2", "idx": 2, "speaker": "bot", "text": "Здравствуйте, я бот"},
            {"uid": "u3", "idx": 3, "speaker": "system", "text": "Личность клиента подтверждена."},
            {"uid": "u4", "idx": 4, "speaker": "operator", "text": "Здравствуйте! Я Лера."},
            {"uid": "u5", "idx": 5, "speaker": "operator", "text": "До свидания!"}]
    c = {"title": "Контакт", "answer_type": "binary", "yes_rule": "y", "no_rule": "n", "focus": ["first_operator"]}
    block = criterion_block({"general_rules": ""}, c, utts)
    assert "[u4] operator: Здравствуйте! Я Лера." in block and "u5" not in block and "бот" not in block
    c["focus"] = ["system_events", "last_turns"]
    block = criterion_block({"general_rules": ""}, c, utts)
    assert "[u3] system" in block and "[u5] operator" in block and block.count("[u3]") == 1
    assert "Ключевые" not in criterion_block({"general_rules": ""}, {**c, "focus": None}, utts)


def test_examples_skip_own_dialog():
    from app.judge import criterion_block
    c = {"title": "X", "answer_type": "binary", "yes_rule": "y", "no_rule": "n",
         "examples": [{"source": "d1", "lines": ["[u1] operator: A"], "answer": "yes", "why": "w1"},
                      {"source": "d2", "lines": ["[u1] operator: B"], "answer": "no", "why": "w2"}]}
    block = criterion_block({"general_rules": ""}, c, [{"uid": "u1", "idx": 1, "speaker": "operator",
                                                      "text": "t", "conversation_id": "d1"}])
    assert "w2" in block and "w1" not in block  # never show the judged dialog's own label


def test_kz_routing():
    from app.judge import settings_for
    s = {"provider": "openai", "model": "cloud-model", "ollama_think": "false",
         "model_kz": "sherkala", "provider_kz": "ollama", "ollama_think_kz": ""}
    kz = settings_for({"language": "KZ"}, s)
    assert (kz["provider"], kz["model"], kz["ollama_think"]) == ("ollama", "sherkala", "")  # main in cloud, KZ local
    assert settings_for({"language": "RU"}, s)["model"] == "cloud-model"
    s = {**s, "provider": "ollama", "model": "qwen3:8b"}
    assert settings_for({"language": "KZ"}, {**s, "model_kz": ""})["model"] == "qwen3:8b"  # routing off
    with TestClient(app) as cli:
        cli.put("/api/settings", json={"model_kz": "sherkala"})
        assert cli.get("/api/settings").json()["model_kz"] == "sherkala"
        cli.put("/api/settings", json={"model_kz": ""})  # clearing must switch routing off
        assert cli.get("/api/settings").json()["model_kz"] == ""
