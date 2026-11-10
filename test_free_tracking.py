
import requests
import json

url = "https://api.navo24.com/v1/tracking/track/public"

payload = {
    "reference": "EGLV050650261124"
}

print("Testing Navo24 public tracking...")
print("BL:", payload["reference"])

try:
    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=60
    )

    print("HTTP Status:", response.status_code)
    print(json.dumps(response.json(), indent=4))

except Exception as error:
    print("Error:", error)
