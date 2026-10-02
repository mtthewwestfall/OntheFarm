import base64
import datetime as dt

import pytest

import server

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "t.db"))
    server.init_db()
    server.app.config["TESTING"] = True
    return server.app


def client(app):
    return app.test_client()


def signup(c, email="owner@farm.test", **kw):
    r = c.post("/api/signup", json={"email": email, "password": "password123",
                                    "name": kw.pop("name", "Owner"), **kw})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture
def owner(app):
    return signup(client(app), farm_name="Willow Creek")


def test_health_and_index(app):
    c = client(app)
    assert c.get("/health").get_json() == {"ok": True}
    r = c.get("/")
    assert r.status_code == 200 and b"OntheFarm" in r.data


def test_auth_flow(app):
    c = client(app)
    assert c.get("/api/me").status_code == 401
    signup(c, farm_name="Willow Creek")
    me = c.get("/api/me").get_json()
    assert me["user"]["role"] == "owner" and me["user"]["farm_name"] == "Willow Creek"
    assert c.post("/api/signup", json={"email": "owner@farm.test", "password": "password123"}).status_code == 400
    c.post("/api/logout")
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"email": "owner@farm.test", "password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"email": "OWNER@farm.test", "password": "password123"}).status_code == 200
    assert c.get("/api/me").status_code == 200


def test_signup_validation(app):
    c = client(app)
    assert c.post("/api/signup", json={"email": "bad", "password": "password123"}).status_code == 400
    assert c.post("/api/signup", json={"email": "a@b.co", "password": "short"}).status_code == 400


def test_crops_crud_and_validation(owner):
    r = owner.post("/api/crops", json={"crop": "Tomatoes", "variety": "Brandywine",
                                       "planted_date": "2026-04-01", "expected_harvest": "2026-07-15",
                                       "area": "Bed 3", "status": "growing", "notes": "staked"})
    assert r.status_code == 201
    cid = r.get_json()["id"]
    assert owner.post("/api/crops", json={"crop": "x", "status": "bogus"}).status_code == 400
    assert owner.post("/api/crops", json={"crop": "x", "planted_date": "April"}).status_code == 400
    assert owner.post("/api/crops", json={"variety": "no crop"}).status_code == 400
    assert owner.put(f"/api/crops/{cid}", json={"status": "harvested"}).get_json()["status"] == "harvested"
    rows = owner.get("/api/crops").get_json()
    assert rows[0]["variety"] == "Brandywine" and rows[0]["status"] == "harvested"
    assert owner.delete(f"/api/crops/{cid}").status_code == 200
    assert owner.get("/api/crops").get_json() == []
    assert owner.get("/api/nonsense").status_code == 404


def test_farm_isolation(app, owner):
    cid = owner.post("/api/crops", json={"crop": "Corn"}).get_json()["id"]
    other = signup(client(app), email="other@farm.test")
    assert other.get("/api/crops").get_json() == []
    assert other.put(f"/api/crops/{cid}", json={"crop": "Stolen"}).status_code == 404
    assert other.delete(f"/api/crops/{cid}").status_code == 404
    aid = owner.post("/api/animals", json={"species": "Goats"}).get_json()["id"]
    r = other.post("/api/feedings", json={"animal_id": aid, "feed_type": "hay", "amount": "1 flake"})
    assert r.status_code == 400


def test_tasks_done_and_priority(owner):
    t = owner.post("/api/tasks", json={"title": "Fix fence", "due_date": "2026-05-01",
                                       "priority": "high"}).get_json()
    assert t["done"] is False or t["done"] == 0
    owner.post("/api/tasks", json={"title": "Order seed", "priority": "low"})
    assert owner.post("/api/tasks", json={"title": "x", "priority": "urgent"}).status_code == 400
    upd = owner.put(f"/api/tasks/{t['id']}", json={"done": True}).get_json()
    assert upd["done"] in (True, 1)
    titles = [x["title"] for x in owner.get("/api/tasks").get_json()]
    assert titles == ["Order seed", "Fix fence"]


def test_animals_with_name_and_instructions(owner):
    a = owner.post("/api/animals", json={"name": "Daisy", "species": "Cow", "breed": "Jersey",
                                         "head_count": 1, "location": "South pasture",
                                         "special_instructions": "Approach from the left",
                                         "health_notes": "Bad hoof"}).get_json()
    assert a["name"] == "Daisy" and a["special_instructions"] == "Approach from the left"
    assert owner.post("/api/animals", json={"species": "Hens", "head_count": -2}).status_code == 400


def test_expenses_and_monthly_totals(owner):
    for d, cat, amt in [("2026-03-02", "Feed", "45.50"), ("2026-03-20", "Feed", 10),
                        ("2026-03-21", "Vet", "$120.00"), ("2026-04-01", "Fuel", "30")]:
        assert owner.post("/api/expenses", json={"date": d, "category": cat, "amount": amt}).status_code == 201
    assert owner.post("/api/expenses", json={"date": "2026-03-01", "category": "x", "amount": "-5"}).status_code == 400
    months = {m["month"]: m for m in owner.get("/api/expense-summary").get_json()}
    assert months["2026-03"]["total"] == 175.5
    assert months["2026-03"]["by_category"] == {"Vet": 120.0, "Feed": 55.5}
    assert months["2026-04"]["total"] == 30.0
    assert owner.get("/api/expenses").get_json()[0]["amount"] == 30.0


def test_sales_paid_pending_and_totals(owner):
    sale = lambda **kw: owner.post("/api/sales", json={"item": "Eggs", "unit": "carton", "per_unit": 12, **kw})
    assert sale(date="2026-06-15", quantity=3, amount="15", customer="Ann", status="paid").status_code == 201
    pend = sale(date="2026-06-16", quantity=2, amount="10", customer="Bob", status="pending").get_json()
    sale(date="2026-06-02", quantity=1, amount="5", customer="Bob", status="pending")
    sale(date="2026-01-10", quantity=4, amount="20", customer="Cy", status="paid")
    sale(date="2025-12-30", quantity=1, amount="99", status="paid")
    assert pend["paid_date"] is None and pend["per_unit"] == 12
    s = owner.get("/api/sales-summary?today=2026-06-17").get_json()
    assert s["week"] == {"start": "2026-06-15", "end": "2026-06-21", "count": 2,
                         "total": 25.0, "paid": 15.0, "pending": 10.0}
    assert (s["month"]["total"], s["month"]["pending"]) == (30.0, 15.0)
    assert (s["year"]["total"], s["year"]["paid"], s["year"]["count"]) == (50.0, 35.0, 4)
    assert s["owed"] == [{"customer": "Bob", "amount": 15.0, "count": 2, "oldest": "2026-06-02"}]
    paid = owner.put(f"/api/sales/{pend['id']}", json={"status": "paid"}).get_json()
    assert paid["status"] == "paid" and paid["paid_date"] == dt.date.today().isoformat()
    assert owner.get("/api/sales-summary?today=2026-06-17").get_json()["owed_total"] == 5.0
    assert sale(date="2026-06-16", amount="1", status="maybe").status_code == 400


def test_goals_progress_and_savings(owner):
    pid = owner.post("/api/projects", json={"name": "Barn roof"}).get_json()["id"]
    g1 = owner.post("/api/goals", json={"title": "Roof done", "kind": "progress", "current": 40}).get_json()
    assert g1["target"] == 100
    g2 = owner.post("/api/goals", json={"title": "Save", "kind": "savings", "target": "2500",
                                        "current": "300", "project_id": pid}).get_json()
    assert owner.put(f"/api/goals/{g2['id']}", json={"current": 450.5}).get_json()["current"] == 450.5
    detail = owner.get(f"/api/projects/{pid}/detail").get_json()
    assert [x["title"] for x in detail["goals"]] == ["Save"]
    assert owner.post("/api/goals", json={"title": "x", "kind": "vibes"}).status_code == 400


def test_feeding_schedule_print_and_guide(owner):
    owner.put("/api/farm", json={"sitter_notes": "Vet: 555-0100"})
    hens = owner.post("/api/animals", json={"name": "Laying flock", "species": "Chickens",
                                            "head_count": 12, "location": "Coop",
                                            "special_instructions": "Lock coop at dusk"}).get_json()["id"]
    goat = owner.post("/api/animals", json={"species": "Goats", "head_count": 3}).get_json()["id"]
    f = owner.post("/api/feedings", json={"animal_id": hens, "feed_type": "Layer pellets", "amount": "2 scoops",
                                          "times": "5:30 pm, 7am", "special_instructions": "Collect eggs"}).get_json()
    assert f["times"] == "07:00, 17:30" and f["times_per_day"] == 2
    owner.post("/api/feedings", json={"animal_id": goat, "feed_type": "Hay", "amount": "1 flake", "times_per_day": 3})
    assert owner.post("/api/feedings", json={"animal_id": goat, "feed_type": "x", "amount": "1",
                                             "times": "25:00"}).status_code == 400
    sched = owner.get("/api/schedule").get_json()
    assert [t["time"] for t in sched["timed"]] == ["07:00", "17:30"]
    assert sched["timed"][0]["animal"] == "Laying flock (Chickens)"
    assert sched["untimed"][0]["times_per_day"] == 3
    page = owner.get("/schedule/print").get_data(as_text=True)
    for text in ("OfftheFARM Instructions", "7:00 AM", "5:30 PM", "Layer pellets", "Lock coop at dusk",
                 "Collect eggs", "Vet: 555-0100", "window.print()", "Laying flock (Chickens)"):
        assert text in page
    gd = owner.get("/api/guide").get_json()
    flock = next(a for a in gd["animals"] if a["name"] == "Laying flock")
    assert flock["special_instructions"] == "Lock coop at dusk"
    assert flock["feedings"][0]["special_instructions"] == "Collect eggs"
    assert gd["owner"]["email"] == "owner@farm.test"
    owner.delete(f"/api/animals/{goat}")
    assert owner.get("/api/schedule").get_json()["untimed"] == []


def test_print_requires_login(app):
    assert client(app).get("/schedule/print").status_code == 401


def test_print_escapes_html(owner):
    aid = owner.post("/api/animals", json={"species": "<script>x</script>"}).get_json()["id"]
    owner.post("/api/feedings", json={"animal_id": aid, "feed_type": "hay", "amount": "1"})
    page = owner.get("/schedule/print").get_data(as_text=True)
    assert "<script>x" not in page and "&lt;script&gt;" in page


def test_member_invite_is_free_and_shares_farm(app, owner):
    owner.post("/api/crops", json={"crop": "Garlic"})
    inv = owner.post("/api/invites", json={"email": "sitter@farm.test"}).get_json()
    assert inv["role"] == "member" and f"invite={inv['token']}" in inv["link"]
    info = client(app).get(f"/api/invites/{inv['token']}").get_json()
    assert info == {"farm_name": "Willow Creek", "email": "sitter@farm.test", "role": "member"}
    m = signup(client(app), email="sitter@farm.test", invite=inv["token"])
    me = m.get("/api/me").get_json()["user"]
    assert me["role"] == "member" and me["farm_name"] == "Willow Creek"
    assert [c["crop"] for c in m.get("/api/crops").get_json()] == ["Garlic"]
    assert m.post("/api/tasks", json={"title": "Water"}).status_code == 201
    assert m.post("/api/invites", json={}).status_code == 403
    assert client(app).post("/api/signup", json={"email": "x@y.zz", "password": "password123",
                                                 "invite": inv["token"]}).status_code == 400
    members = owner.get("/api/members").get_json()
    assert {x["role"] for x in members} == {"owner", "member"}
    uid = next(x["id"] for x in members if x["role"] == "member")
    assert owner.delete(f"/api/members/{uid}").status_code == 200
    assert m.get("/api/crops").status_code == 401


def test_guest_sees_only_offthefarm(app, owner):
    aid = owner.post("/api/animals", json={"name": "Daisy", "species": "Cow",
                                           "special_instructions": "Gentle"}).get_json()["id"]
    owner.post("/api/feedings", json={"animal_id": aid, "feed_type": "Hay", "amount": "2 flakes", "times": "6am"})
    owner.post("/api/sales", json={"date": "2026-01-01", "item": "Milk", "amount": 5, "status": "paid"})
    inv = owner.post("/api/invites", json={"role": "guest"}).get_json()
    assert inv["role"] == "guest"
    assert owner.post("/api/invites", json={"role": "admin"}).status_code == 400
    gst = signup(client(app), email="guest@farm.test", invite=inv["token"])
    assert gst.get("/api/me").get_json()["user"]["role"] == "guest"
    gd = gst.get("/api/guide").get_json()
    assert gd["animals"][0]["name"] == "Daisy" and gd["animals"][0]["special_instructions"] == "Gentle"
    assert gst.get("/api/schedule").status_code == 200
    assert "Daisy (Cow)" in gst.get("/schedule/print").get_data(as_text=True)
    for path in ("/api/sales", "/api/animals", "/api/expense-summary", "/api/members", "/api/sales-summary"):
        assert gst.get(path).status_code == 403, path
    assert gst.post("/api/animals", json={"species": "Pig"}).status_code == 403
    assert gst.put("/api/farm", json={"sitter_notes": "hi"}).status_code == 403
    uid = next(x["id"] for x in owner.get("/api/members").get_json() if x["role"] == "guest")
    assert owner.delete(f"/api/members/{uid}").status_code == 200


def test_revoke_invite(app, owner):
    inv = owner.post("/api/invites", json={}).get_json()
    assert len(owner.get("/api/invites").get_json()) == 1
    owner.delete(f"/api/invites/{inv['token']}")
    assert client(app).get(f"/api/invites/{inv['token']}").status_code == 404


def test_photos_upload_validation_and_access(app, owner):
    pid = owner.post("/api/projects", json={"name": "Shed"}).get_json()["id"]
    ph = owner.post(f"/api/projects/{pid}/photos", json={"data": PNG_URL})
    assert ph.status_code == 201
    url = ph.get_json()["url"]
    r = owner.get(url)
    assert r.status_code == 200 and r.data == PNG and r.mimetype == "image/png"
    assert owner.post(f"/api/projects/{pid}/photos", json={"data": "data:text/plain;base64,aGk="}).status_code == 400
    assert owner.post(f"/api/projects/{pid}/photos", json={}).status_code == 400
    big = "data:image/png;base64," + base64.b64encode(b"0" * (server.MAX_PHOTO_BYTES + 1)).decode()
    assert owner.post(f"/api/projects/{pid}/photos", json={"data": big}).status_code in (400, 413)
    other = signup(client(app), email="other@farm.test")
    assert other.get(url).status_code == 404
    assert other.post(f"/api/projects/{pid}/photos", json={"data": PNG_URL}).status_code == 404


def test_visualize_uses_owner_plan(owner, monkeypatch):
    seen = {}

    def fake(image, mime, prompt):
        seen.update(image=image, prompt=prompt)
        return b"RENDER", "image/png"

    monkeypatch.setattr(server, "generate_visualization", fake)
    pid = owner.post("/api/projects", json={"name": "Goat shed", "plan": "Red metal roof, pole frame"}).get_json()["id"]
    assert owner.post(f"/api/projects/{pid}/visualize", json={}).status_code == 400
    site = owner.post(f"/api/projects/{pid}/photos", json={"data": PNG_URL}).get_json()
    r = owner.post(f"/api/projects/{pid}/visualize", json={"photo_id": site["id"], "details": "door faces pond"})
    assert r.status_code == 201
    assert seen["image"] == PNG and "Red metal roof, pole frame" in seen["prompt"] and "door faces pond" in seen["prompt"]
    assert owner.get(r.get_json()["url"]).data == b"RENDER"
    kinds = [p["kind"] for p in owner.get(f"/api/projects/{pid}/detail").get_json()["photos"]]
    assert kinds == ["site", "render"]


def test_visualize_ai_error(owner, monkeypatch):
    def boom(*a):
        raise server.AIError("down")
    monkeypatch.setattr(server, "generate_visualization", boom)
    pid = owner.post("/api/projects", json={"name": "Coop"}).get_json()["id"]
    owner.post(f"/api/projects/{pid}/photos", json={"data": PNG_URL})
    r = owner.post(f"/api/projects/{pid}/visualize", json={})
    assert r.status_code == 502 and r.get_json()["error"] == "down"


def test_advice_given_exactly_once(owner, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "generate_advice", lambda prompt, image: calls.append(prompt) or "Use gravel pad")
    pid = owner.post("/api/projects", json={"name": "Shed", "plan": "Concrete slab"}).get_json()["id"]
    r = owner.post(f"/api/projects/{pid}/advice", json={}).get_json()
    assert r == {"advice": "Use gravel pad", "advice_status": "given"}
    assert "Concrete slab" in calls[0]
    for body in ({}, {"decline": True}):
        assert owner.post(f"/api/projects/{pid}/advice", json=body).get_json()["advice_status"] == "given"
    owner.put(f"/api/projects/{pid}", json={"plan": "Still concrete"})
    owner.post(f"/api/projects/{pid}/advice", json={})
    assert len(calls) == 1
    p = owner.get(f"/api/projects/{pid}/detail").get_json()["project"]
    assert p["plan"] == "Still concrete" and p["advice_status"] == "given"


def test_advice_decline_is_final(owner, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "generate_advice", lambda *a: calls.append(1) or "x")
    pid = owner.post("/api/projects", json={"name": "Fence"}).get_json()["id"]
    assert owner.post(f"/api/projects/{pid}/advice", json={"decline": True}).get_json()["advice_status"] == "declined"
    assert owner.post(f"/api/projects/{pid}/advice", json={}).get_json()["advice_status"] == "declined"
    assert calls == []


def test_advice_failure_can_retry(owner, monkeypatch):
    def boom(*a):
        raise server.AIError("busy")
    monkeypatch.setattr(server, "generate_advice", boom)
    pid = owner.post("/api/projects", json={"name": "Pond"}).get_json()["id"]
    assert owner.post(f"/api/projects/{pid}/advice", json={}).status_code == 502
    monkeypatch.setattr(server, "generate_advice", lambda *a: "ok")
    assert owner.post(f"/api/projects/{pid}/advice", json={}).get_json()["advice_status"] == "given"


def test_project_delete_cascades(owner):
    pid = owner.post("/api/projects", json={"name": "Barn"}).get_json()["id"]
    owner.post("/api/tasks", json={"title": "Pour footings", "project_id": pid, "due_date": "2026-08-01"})
    owner.post("/api/tasks", json={"title": "Unrelated"})
    gid = owner.post("/api/goals", json={"title": "Fund", "kind": "savings", "project_id": pid}).get_json()["id"]
    owner.post(f"/api/projects/{pid}/photos", json={"data": PNG_URL})
    assert [t["title"] for t in owner.get(f"/api/tasks?project_id={pid}").get_json()] == ["Pour footings"]
    owner.delete(f"/api/projects/{pid}")
    assert [t["title"] for t in owner.get("/api/tasks").get_json()] == ["Unrelated"]
    assert next(x for x in owner.get("/api/goals").get_json() if x["id"] == gid)["project_id"] is None


def test_shop_search(owner, monkeypatch):
    seen = {}

    def fake_gemini(model, parts, timeout=120, tools=None, full=False):
        seen.update(prompt=parts[0]["text"], tools=tools)
        return {"content": {"parts": [{"text": 'Sure:\n```json\n[{"name":"Layer feed 50 lb","store":"TSC",'
                                                 '"price":"$18.99","url":"https://tsc.example/feed","note":"in stock"},'
                                                 '{"name":"Bad link","url":"javascript:alert(1)"},{"store":"no name"}]\n```'}]},
                "groundingMetadata": {"groundingChunks": [{"web": {"title": "tsc.example", "uri": "https://r.example/1"}}]}}

    monkeypatch.setattr(server, "_gemini", fake_gemini)
    owner.put("/api/farm", json={"location": "Medina, OH"})
    assert owner.post("/api/shop/search", json={"query": ""}).status_code == 400
    r = owner.post("/api/shop/search", json={"query": "layer feed", "category": "feed"}).get_json()
    assert seen["tools"] == [{"google_search": {}}] and "Medina, OH" in seen["prompt"]
    assert [i["name"] for i in r["items"]] == ["Layer feed 50 lb", "Bad link"]
    assert r["items"][1]["url"] == "" and r["sources"] == [{"title": "tsc.example", "url": "https://r.example/1"}]
    owner.post("/api/shop/search", json={"query": "t-posts", "category": "materials", "nearby": False})
    assert "Medina" not in seen["prompt"]


def test_shop_ai_error(owner, monkeypatch):
    def boom(*a, **k):
        raise server.AIError("no key")
    monkeypatch.setattr(server, "_gemini", boom)
    assert owner.post("/api/shop/search", json={"query": "hay"}).status_code == 502


def test_parse_times():
    assert server.parse_times("6am, 12:30 PM;18:00") == ["06:00", "12:30", "18:00"]
    assert server.parse_times("12am") == ["00:00"]
    with pytest.raises(server.BadRequest):
        server.parse_times("noonish")


def test_tab_labels_defaults_and_rename(owner):
    labels = owner.get("/api/tab-labels").get_json()
    assert labels["shop"] == "Leroy's feed/parts store"
    assert labels["sales"] == "Gracie's sales corner"
    assert len(labels) == 11
    r = owner.put("/api/tab-labels", json={"labels": {"shop": "Feed Barn", "tasks": "  Chores  "}})
    assert r.status_code == 200
    labels = r.get_json()
    assert labels["shop"] == "Feed Barn" and labels["tasks"] == "Chores"
    assert owner.get("/api/tab-labels").get_json()["shop"] == "Feed Barn"


def test_tab_labels_reset_and_validation(owner):
    owner.put("/api/tab-labels", json={"labels": {"shop": "Feed Barn"}})
    r = owner.put("/api/tab-labels", json={"labels": {"shop": "   "}})
    assert r.get_json()["shop"] == "Leroy's feed/parts store"
    assert owner.put("/api/tab-labels", json={"labels": {"nope": "x"}}).status_code == 400
    assert owner.put("/api/tab-labels", json={"labels": {"shop": "x" * 41}}).status_code == 400
    assert owner.put("/api/tab-labels", json={"labels": "nope"}).status_code == 400
    assert owner.put("/api/tab-labels", json={}).status_code == 400


def test_tab_labels_guest_and_farm_scoping(app, owner):
    inv = owner.post("/api/invites", json={"role": "guest"}).get_json()
    gst = signup(client(app), email="guest@farm.test", invite=inv["token"])
    assert gst.get("/api/tab-labels").get_json()["shop"] == "Leroy's feed/parts store"
    assert gst.put("/api/tab-labels", json={"labels": {"shop": "x"}}).status_code == 403
    assert client(app).get("/api/tab-labels").status_code == 401
    owner.put("/api/tab-labels", json={"labels": {"shop": "Farm One Store"}})
    other = signup(client(app), email="other@farm.test", farm_name="Second Farm")
    assert other.get("/api/tab-labels").get_json()["shop"] == "Leroy's feed/parts store"
