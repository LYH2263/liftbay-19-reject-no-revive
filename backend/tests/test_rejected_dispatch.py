"""满员拒绝（rejected）的终态语义：

- 拒绝后立刻再派 → 失败，提示重新登记
- 轿厢清客/载荷下降腾出容量后，旧单仍失败
- 新登记的 waiting 单可正常派工
- 回放保留当初满员原因
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Building, ElevatorCar


@pytest.fixture()
def env():
    # 内存 sqlite 替代 postgres；不进入 lifespan，避免触碰真实库与种子数据
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), TestingSession
    app.dependency_overrides.clear()


def seed_building_with_full_car(Session, capacity=2):
    db = Session()
    b = Building(name="测试楼", floors=10)
    db.add(b)
    db.flush()
    car = ElevatorCar(
        building_id=b.id, label="T1", floor=1, direction="idle",
        load=capacity, capacity=capacity,
    )
    db.add(car)
    db.commit()
    ids = (b.id, car.id)
    db.close()
    return ids


def free_car(Session, car_id):
    """模拟轿厢清客：载荷归零，腾出容量。"""
    db = Session()
    car = db.get(ElevatorCar, car_id)
    car.load = 0
    db.commit()
    db.close()


def register_call(client, building_id, floor=3, passengers=1):
    r = client.post(
        "/api/calls",
        json={
            "building_id": building_id,
            "floor": floor,
            "direction": "up",
            "passengers": passengers,
        },
    )
    assert r.status_code == 200
    return r.json()["id"]


def test_rejected_call_cannot_be_redispatched_immediately(env):
    client, Session = env
    bid, _car_id = seed_building_with_full_car(Session)
    call_id = register_call(client, bid)

    r1 = client.post("/api/dispatch", json={"call_id": call_id})
    assert r1.status_code == 409
    calls = client.get("/api/calls").json()
    assert calls[0]["status"] == "rejected"

    r2 = client.post("/api/dispatch", json={"call_id": call_id})
    assert r2.status_code == 409
    assert "重新登记" in r2.json()["detail"]
    # 状态不因重复派工而改变
    calls = client.get("/api/calls").json()
    assert calls[0]["status"] == "rejected"


def test_rejected_call_stays_rejected_after_capacity_frees(env):
    client, Session = env
    bid, car_id = seed_building_with_full_car(Session)
    call_id = register_call(client, bid)
    assert client.post("/api/dispatch", json={"call_id": call_id}).status_code == 409

    free_car(Session, car_id)  # 腾出容量

    r = client.post("/api/dispatch", json={"call_id": call_id})
    assert r.status_code == 409
    assert "重新登记" in r.json()["detail"]
    calls = client.get("/api/calls").json()
    assert calls[0]["status"] == "rejected"


def test_new_waiting_call_dispatches_after_capacity_frees(env):
    client, Session = env
    bid, car_id = seed_building_with_full_car(Session)
    old_id = register_call(client, bid)
    assert client.post("/api/dispatch", json={"call_id": old_id}).status_code == 409

    free_car(Session, car_id)

    new_id = register_call(client, bid, floor=4)
    r = client.post("/api/dispatch", json={"call_id": new_id})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "assigned"
    assert body["assigned_car_id"] == car_id


def test_replay_preserves_full_capacity_reason(env):
    client, Session = env
    bid, _car_id = seed_building_with_full_car(Session)
    call_id = register_call(client, bid, floor=6, passengers=2)
    assert client.post("/api/dispatch", json={"call_id": call_id}).status_code == 409

    logs = client.get("/api/replay").json()
    entries = [l for l in logs if l["call_id"] == call_id]
    assert len(entries) == 1  # 重复/后续派工不产生新日志
    entry = entries[0]
    assert entry["car_id"] is None
    assert "满员" in entry["detail"]
    assert "6 层" in entry["detail"] and "2 人" in entry["detail"]
