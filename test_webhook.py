import requests
import json

BASE_URL = "http://localhost:8001"

print("=" * 50)
print("Orquestrator ISA — Tests de Webhook")
print("=" * 50)

# Test 1: Health Check
print("\nTest 1: Health Check")
try:
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    print(f"   Status: {r.status_code}")
    print(f"   Body: {r.json()}")
    if r.status_code == 200:
        print("   ✅ Health Check OK")
    else:
        print("   ❌ Health Check FALLÓ")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 2: Webhook Verification (GET)
print("\nTest 2: Webhook Verification (GET)")
try:
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": "isa_verify_2026",
        "hub.challenge": "123456789"
    }
    r = requests.get(f"{BASE_URL}/api/whatsapp/webhook", params=params, timeout=5)
    print(f"   Status: {r.status_code}")
    print(f"   Challenge response: {r.text}")
    if r.status_code == 200 and r.text == "123456789":
        print("   ✅ Verificacion OK")
    else:
        print("   ❌ Verificacion FALLÓ")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 3: Webhook Message (POST)
print("\nTest 3: Webhook Message (POST)")
try:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "2808743646146108",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "212626282904",
                        "phone_number_id": "1097255916805484"
                    },
                    "contacts": [{
                        "profile": {"name": "Test User"},
                        "wa_id": "212600000000"
                    }],
                    "messages": [{
                        "from": "212600000000",
                        "id": "wamid.test123",
                        "timestamp": "1714740000",
                        "type": "text",
                        "text": {"body": "Hola, quiero pedir un café"}
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    
    r = requests.post(
        f"{BASE_URL}/api/whatsapp/webhook",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=5
    )
    print(f"   Status: {r.status_code}")
    
    # El body puede estar vacío — no forzar JSON
    if r.text:
        try:
            print(f"   Body: {r.json()}")
        except:
            print(f"   Body (text): {r.text}")
    else:
        print("   Body: (vacío — esto es normal para webhooks)")
    
    if r.status_code == 200:
        print("   ✅ Mensaje POST OK")
    else:
        print("   ❌ Mensaje POST FALLÓ")
        
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "=" * 50)
print("Tests completados")
print("=" * 50)
