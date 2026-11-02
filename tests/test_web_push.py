import json
import stat

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


def test_push_endpoints_only_accept_supported_secure_services():
    assert main.is_supported_push_endpoint("https://fcm.googleapis.com/fcm/send/token")
    assert main.is_supported_push_endpoint("https://wns2.example.notify.windows.com/path")
    assert not main.is_supported_push_endpoint("http://fcm.googleapis.com/fcm/send/token")
    assert not main.is_supported_push_endpoint("https://127.0.0.1/private")
    assert not main.is_supported_push_endpoint("https://example.com/push")


def test_parse_push_subscription_validates_and_normalizes_payload():
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/token",
        "expirationTime": None,
        "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
    }

    subscription = main.parse_push_subscription(payload)

    assert subscription == {
        "endpoint": payload["endpoint"],
        "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
    }


def test_parse_push_subscription_rejects_untrusted_endpoint():
    with pytest.raises(HTTPException) as error:
        main.parse_push_subscription(
            {
                "endpoint": "https://example.com/push",
                "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
            }
        )

    assert error.value.status_code == 422


def test_vapid_key_is_generated_once_with_private_permissions(tmp_path, monkeypatch):
    key_path = tmp_path / "vapid.pem"
    monkeypatch.setattr(main, "PUSH_PRIVATE_KEY_FILE", key_path)

    first_path, first_public_key = main.load_or_create_vapid_key()
    second_path, second_public_key = main.load_or_create_vapid_key()

    assert first_path == second_path == key_path
    assert first_public_key == second_public_key
    assert len(first_public_key) == 87
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_expiry_push_is_sent_once_per_subscription_and_expiry_date(tmp_path, monkeypatch):
    subscription = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/token",
        "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
    }
    food = {
        "id": "food-1",
        "name": "牛乳",
        "expiry_date": (main.date.today() + main.timedelta(days=2)).isoformat(),
        "expiry_type": "best_before",
        "category": "その他",
        "eaten": False,
    }
    data_file = tmp_path / "foods.json"
    data_file.write_text(json.dumps([food], ensure_ascii=False), encoding="utf-8")
    subscriptions_file = tmp_path / "subscriptions.json"
    subscriptions_file.write_text(json.dumps([subscription]), encoding="utf-8")
    monkeypatch.setattr(main, "DATA_FILE", data_file)
    monkeypatch.setattr(main, "PUSH_SUBSCRIPTIONS_FILE", subscriptions_file)
    monkeypatch.setattr(main, "PUSH_DELIVERIES_FILE", tmp_path / "deliveries.json")
    sent = []
    monkeypatch.setattr(main, "send_web_push", lambda sub, payload: sent.append((sub, payload)))

    main.send_pending_expiry_notifications()
    main.send_pending_expiry_notifications()

    assert len(sent) == 1
    assert "牛乳" in sent[0][1]["body"]


def test_notification_routes_serve_setup_and_save_subscriptions(tmp_path, monkeypatch):
    subscriptions_path = tmp_path / "subscriptions.json"
    monkeypatch.setattr(main, "PUSH_PRIVATE_KEY_FILE", tmp_path / "vapid.pem")
    monkeypatch.setattr(main, "PUSH_SUBSCRIPTIONS_FILE", subscriptions_path)
    monkeypatch.setattr(main, "PUSH_DELIVERIES_FILE", tmp_path / "deliveries.json")
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "foods.json")

    with TestClient(main.create_app()) as client:
        settings = client.get("/notifications")
        worker = client.get("/service-worker.js")
        public_key = client.get("/push/vapid-public-key")
        payload = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/token",
            "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
        }
        saved = client.post("/push/subscriptions", json=payload)

    assert settings.status_code == 200
    assert "通知を有効にする" in settings.text
    assert worker.status_code == 200
    assert worker.headers["service-worker-allowed"] == "/"
    assert "showNotification" in worker.text
    assert public_key.status_code == 200
    assert len(public_key.json()["publicKey"]) == 87
    assert saved.status_code == 200
    assert json.loads(subscriptions_path.read_text(encoding="utf-8"))[0]["endpoint"] == payload["endpoint"]
