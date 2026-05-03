#!/usr/bin/env python3
"""Script de prueba para el webhook de Orquestrator ISA"""

import requests

BASE_URL = "http://localhost:8001"
WEBHOOK_URL = f"{BASE_URL}/api/whatsapp/webhook"
VERIFY_TOKEN = "isa_verify_2026"

def test_health():
    print("Test 1: Health Check")
    r = requests.get(f"{BASE_URL}/health")
    print(f"   Status: {r.status_code}")
    print(f"   Body: {r.json()}")
    print()

def test_webhook_verify():
    print("Test 2: Webhook Verification (GET)")
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "123456789"
    }
    r = requests.get(WEBHOOK_URL, params=params)
    print(f"   Status: {r.status_code}")
    print(f"   Challenge response: {r.text}")
    assert r.status_code == 200, "Verificacion fallida"
    assert r.text == "123456789", "Challenge no coincide"
    print("   Verificacion OK")
    print()

def test_webhook_message():
    print("Test 3: Webhook Message (POST)")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "test_business_id",
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "212600000000",
                        "id": "test_message_id",
                        "timestamp": "1714651200",
                        "type": "text",
                        "text": {"body": "hola"}
                    }]
                }
            }]
        }]
    }
    r = requests.post(WEBHOOK_URL, json=payload)
    print(f"   Status: {r.status_code}")
    print(f"   Body: {r.json()}")
    assert r.status_code == 200
    print("   Mensaje procesado")
    print()

def test_stats():
    print("Test 4: Stats API")
    r = requests.get(f"{BASE_URL}/api/stats")
    print(f"   Status: {r.status_code}")
    print(f"   Body: {r.json()}")
    print()

def test_restaurantes():
    print("Test 5: Restaurantes API")
    r = requests.get(f"{BASE_URL}/api/restaurantes")
    print(f"   Status: {r.status_code}")
    print(f"   Body: {r.json()}")
    print()

if __name__ == "__main__":
    print("=" * 50)
    print("Orquestrator ISA — Tests de Webhook")
    print("=" * 50)
    print()

    try:
        test_health()
        test_webhook_verify()
        test_webhook_message()
        test_stats()
        test_restaurantes()
        print("=" * 50)
        print("Todos los tests pasaron")
        print("=" * 50)
    except Exception as e:
        print(f"Error: {e}")
        print("Asegurate de que el servidor esta corriendo en localhost:8001")
