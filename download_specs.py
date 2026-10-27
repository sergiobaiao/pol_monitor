import urllib.request
import os
import ssl

token = os.environ["POL_TOKEN"]  # JWT do POL ADV (nunca versionar)

os.makedirs(r"z:\pol\swagger", exist_ok=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

urls = [
    ("polgateway-api.json", "https://office-api.pol.adv.br/v3/api-docs", {}),
    ("polgateway-management-api.json", "https://office-api.pol.adv.br/v3/api-docs?group=management", {"Authorization": f"Bearer {token}"}),
    ("lawoffice-service-api.json", "https://office-api.pol.adv.br/services/lawoffice/v3/api-docs", {"Authorization": f"Bearer {token}"}),
]

for filename, url, headers in urls:
    print(f"Downloading {filename} from {url}...")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            content = response.read()
            outpath = os.path.join(r"z:\pol\swagger", filename)
            with open(outpath, "wb") as f:
                f.write(content)
            print(f"Saved {filename} ({len(content)} bytes)")
    except Exception as e:
        print(f"Failed {filename}: {e}")
