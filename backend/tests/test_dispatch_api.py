"""rejected 终态：满员拒绝后不可再次派工，腾出容量也不复活，新 waiting 单可派。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Building, CallTicket, DispatchLog, ElevatorCar


@pytest.fixture()
def ctx():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    db = TestSession()
    b = Building(name="测试楼", floors=18)
    db.add(b)
    db.flush()
    car = ElevatorCar(
        building_id=b.id, label="T1", floor=1, direction="idle", load=8, capacity=8
    )
    db.add(car)
    db.commit()

    def _get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    try:
        yield {
            "client": TestClient(app),
            "Session": TestSession,
            "building_id": b.id,
            "car_id": car.id,
        }
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def _register(client, building_id: int, passengers: int = 1) -> int:
    r = client.post(
        "/api/calls",
        json={
            "building_id": building_id,
            "floor": 1,
            "direction": "up",
            "passengers": passengers,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting"
    return body["id"]


def test_rejected_ticket_stays_rejected_after_capacity_freed(ctx):
    client = ctx["client"]
    Session = ctx["Session"]
    bid, car_id = ctx["building_id"], ctx["car_id"]

    call_id = _register(client, bid)

    # 全部轿厢满员：首次派工被拒，工单进入 rejected 终态
    r = client.post("/api/dispatch", json={"call_id": call_id})
    assert r.status_code == 409
    assert "满员" in r.json()["detail"]
    with Session() as s:
        assert s.get(CallTicket, call_id).status == "rejected"

    # 拒绝后立刻再派：同样失败，并提示重新登记
    r = client.post("/api/dispatch", json={"call_id": call_id})
    assert r.status_code == 409
    assert "重新登记" in r.json()["detail"]
    with Session() as s:
        assert s.get(CallTicket, call_id).status == "rejected"

    # 轿厢清客腾出容量，旧 rejected 单仍不可派
    with Session() as s:
        s.get(ElevatorCar, car_id).load = 0
        s.commit()
    r = client.post("/api/dispatch", json={"call_id": call_id})
    assert r.status_code == 409
    with Session() as s:
        ticket = s.get(CallTicket, call_id)
        assert ticket.status == "rejected"
        assert ticket.assigned_car_id is None
        assert s.get(ElevatorCar, car_id).load == 0  # 旧单被拒不应改动轿厢

    # 容量恢复后新登记的 waiting 单可以正常派工
    new_id = _register(client, bid)
    r = client.post("/api/dispatch", json={"call_id": new_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "assigned"
    assert body["assigned_car_id"] == car_id
    with Session() as s:
        assert s.get(ElevatorCar, car_id).load == 1


def test_replay_keeps_original_full_reason(ctx):
    client = ctx["client"]
    Session = ctx["Session"]
    bid = ctx["building_id"]
    call_id = _register(client, bid)

    assert client.post("/api/dispatch", json={"call_id": call_id}).status_code == 409
    # 再派两次，均不应新增日志
    for _ in range(2):
        assert client.post("/api/dispatch", json={"call_id": call_id}).status_code == 409

    with Session() as s:
        logs = s.scalars(
            select(DispatchLog).where(DispatchLog.call_id == call_id)
        ).all()
        assert len(logs) == 1
        assert logs[0].car_id is None
        assert "满员" in logs[0].detail
