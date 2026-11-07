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
        tr = cli.get(f"/api/criterion-results/{ident['result']['id']}/trace").json()
        assert len(tr["calls"]) >= 1 and tr["calls"][0]["status"] == "ok" and tr["raw_output"]
        assert "<transcript>" in tr["prompt"]["user"] and "Идентификация" in tr["prompt"]["user"]
        assert cli.get("/api/criterion-results/999999/trace").status_code == 404

        # live re-run ignoring the cache: 8 fresh model calls, same deterministic answers
        calls0 = cli.get("/api/stats/cost").json()["calls"]
        fresh = wait_run(cli, cli.post("/api/evaluations", json={"conversation_id": "f-0", "force": True}).json()["id"])
        assert fresh["new_calls"] == 8 and fresh["cache_hits"] == 0
        assert cli.get("/api/stats/cost").json()["calls"] - calls0 == 8
        again = cli.get("/api/conversations/f-0").json()["view"]
        assert again["breakdown"]["total"] == view["breakdown"]["total"]

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
        # learning from appeals: the reviewer's contact correction on f-0 is now an example, so contact
        # re-runs for f-1 and f-2 (their prompt changed) but not for f-0 itself; empathy re-runs everywhere
        from app.judge import appeal_examples
        from app.checklist import get_version
        v2 = get_version(saved["id"])["checklist"]
        contact_c = next(c for c in v2["criteria"] if c["id"] == "contact")
        ex = appeal_examples(v2, contact_c, "f-1")
        assert len(ex) == 1 and ex[0]["answer"] == "no" and "Имени нет" in ex[0]["why"]
        assert appeal_examples(v2, contact_c, "f-0") == []
        run3 = wait_run(cli, cli.post("/api/runs", json={"dataset_group_id": "FLOW"}).json()["id"])
        assert run3["new_calls"] == 5 and run3["cache_hits"] == 19

        # invalid checklist is rejected with a readable message
        cl["criteria"][0]["weight"] = -1
        r = cli.post("/api/checklist/versions", json={"checklist": cl})
        assert r.status_code == 400 and "вес" in r.json()["detail"]

        assert cli.get("/api/export/FLOW.csv").status_code == 200
        res = cli.get("/api/export/FLOW/results.json").json()
        assert res["schema_version"] == "1.0" and len(res["results"]) == 3
        r0 = next(r for r in res["results"] if r["conversation_id"] == "f-0")
        assert r0["status"] == "ok" and len(r0["criteria"]) == 8
        by = {c["criterion_id"]: c for c in r0["criteria"]}
        assert set(by) == {"contact", "discovery", "identification", "consultation", "empathy",
                           "dialogue", "closure", "critical"}
        assert by["identification"]["value"] is None          # N/A -> null
        assert by["contact"]["value"] is True  # active version is v2 now; the v1 appeal stays with v1
        assert by["consultation"]["value"] == 20 and isinstance(by["dialogue"]["value"], int)
        assert by["critical"]["value"] is False and r0["score"]["critical_flag"] is False
        assert all(isinstance(t, int) for c in r0["criteria"] for t in c["evidence_turns"])

        an = cli.get("/api/analytics?dataset=FLOW").json()
        assert an["evaluated"] == 3 and an["avg_total"] == 100.0 and len(an["criteria"]) == 8
        ident = next(c for c in an["criteria"] if c["id"] == "identification")
        assert ident["na"] == 3 and ident["met_pct"] is None
        assert sum(b["count"] for b in an["distribution"]) == 3
        assert cli.get("/api/stats/cost").json()["calls"] == 37  # includes the 8 forced live re-run calls


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
    utts.append({"uid": "u6", "idx": 6, "speaker": "client", "text": "В каком меню?"})
    block = criterion_block({"general_rules": ""}, {**c, "focus": ["client_followups"]}, utts)
    assert "[u6] client: В каком меню?" in block and "[u1]" not in block  # only after the operator joined


def test_examples_skip_own_dialog():
    from app.judge import criterion_block
    c = {"title": "X", "answer_type": "binary", "yes_rule": "y", "no_rule": "n",
         "examples": [{"source": "d1", "lines": ["[u1] operator: A"], "answer": "yes", "why": "w1"},
                      {"source": "d2", "lines": ["[u1] operator: B"], "answer": "no", "why": "w2"}]}
    block = criterion_block({"general_rules": ""}, c, [{"uid": "u1", "idx": 1, "speaker": "operator",
                                                      "text": "t", "conversation_id": "d1"}])
    assert "w2" in block and "w1" not in block  # never show the judged dialog's own label


def test_ensemble_voting():
    recs = [{"conversation_id": "ens-1", "channel": "agent chat", "language": "RU", "audio_url": None,
             "transcript": [{"timestamp": "00:01", "speaker": "client", "text": "Вопрос про тариф (ens)"},
                            {"timestamp": "00:05", "speaker": "operator", "text": "Здравствуйте! Я Лера. Ответ."}]}]
    with TestClient(app) as cli:
        assert cli.post("/api/datasets/upload", files={"file": ("e.json", json.dumps(recs))},
                        data={"group_id": "ENS"}).status_code == 200
        assert cli.put("/api/settings", json={"ensemble_k": "12"}).status_code == 400
        cli.put("/api/settings", json={"ensemble_k": "3", "ensemble_criteria": "needs,empathy"})
        before = cli.get("/api/stats/cost").json()["calls"]
        run = wait_run(cli, cli.post("/api/runs", json={"dataset_group_id": "ENS"}).json()["id"])
        assert run["status"] == "done"
        # 6 single-call criteria + 2 voted criteria x 3 samples = 12 calls
        assert cli.get("/api/stats/cost").json()["calls"] - before == 12
        items = {i["criterion"]["id"]: i for i in cli.get("/api/conversations/ens-1").json()["view"]["items"]}
        assert items["empathy"]["result"]["votes"] == "3/3" and items["empathy"]["result"]["confidence"] == "high"
        assert items["contact"]["result"]["votes"] is None
        cli.put("/api/settings", json={"ensemble_k": "1"})  # leave the shared test DB as other tests expect


def test_num_ctx_auto():
    from app.providers import pick_num_ctx
    s = {"ollama_num_ctx": "12288"}
    assert pick_num_ctx(s, "x" * 2000, "y" * 7000) == 8192        # a normal chat prompt
    assert pick_num_ctx(s, "x" * 2000, "y" * 20000) == 12288      # a long call transcript
    assert pick_num_ctx({"ollama_num_ctx": "4096"}, "x", "y") == 4096  # never above the configured max


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
